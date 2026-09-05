"""조사 결과(discovered.json)를 출처 등록부로 바꾼다.

만드는 파일:
  registry/bootstrap/organizations.json  대학·캠퍼스·조직
  registry/bootstrap/sources.json        수집 출처

원칙(5절):
- 조직 이름은 공식 원문에서만 가져온다. 못 찾으면 이름을 지어내지 않고 needs_review 로 남긴다.
- 공지 성격이 아닌 게시판(교수소개·교육과정·자료실 등)은 출처로 만들지 않는다.
- 대표 표본만 status=active 로 두고 나머지는 pending 이다. 검증 뒤 ops 로 승격한다.
- 대상 범위 기본값은 출처의 소속 조직이다. 본부 게시판을 전교생 대상으로 단정하지 않는다(4절 4항).

사용: python scripts/build_registry.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO_ROOT / "registry" / "bootstrap"
UA = "khu-notice-bot/0.1 (+https://github.com/sihort183-creator/khu)"

MAIN_URL = "https://www.khu.ac.kr/kor/user/main/view.do"

# 공지 성격의 게시판만 출처로 만든다.
NOTICE_LABEL = re.compile(
    r"공지|알림|소식|안내|뉴스|새소식|공고|장학|취업|채용|모집|행사|일정|학사|"
    r"프로그램|비교과|공모|자유게시|묻고|FAQ|보도"
)
# 공지가 아닌 것이 확실한 게시판.
NOT_NOTICE_LABEL = re.compile(
    r"교수\s*소개|교수진|석좌교수|명예교수|교육과정|커리큘럼|시설|둘러보기|갤러리|"
    r"동영상|영상|사진|자료실|논문|연구실|졸업생|동문|채용시스템|규정|서식"
)

# 본부(www)에서 실제로 학생에게 필요한 공지 게시판만 고른다.
HQ_ALLOW = re.compile(r"공지|공고|장학|학사|모집|채용|행사|소식|알림|입찰|안내")

CAMPUS_SEOUL = "seoul"
CAMPUS_GLOBAL = "global"

_LOGO_ALT = re.compile(r'alt="([^"]{2,40})"')
_CLEAN_NAME = re.compile(r"\s*(로고\s*이미지|로고|_?logo|이미지)\s*$", re.IGNORECASE)
# 기관 이름이 아닌 공통 이미지 문구.
_ALT_REJECT = re.compile(r"푸터|footer|배너|banner|바로가기|Give|발전기금|메인|상단|하단|슬라이드", re.IGNORECASE)


def fetch(url: str, timeout: float = 20.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return ""


def main_site_names() -> dict[str, str]:
    """본부 첫 화면의 링크 글자에서 기관 이름을 모은다. 공식 원문이다."""
    html = fetch(MAIN_URL)
    names: dict[str, str] = {}
    pattern = re.compile(
        r'<a[^>]+href="https?://([a-z0-9-]+)\.khu\.ac\.kr[^"]*"[^>]*>(.*?)</a>', re.S
    )
    for sub, raw in pattern.findall(html):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()
        if not text or not (2 <= len(text) <= 30):
            continue
        if text in ("바로가기", "더보기", "링크"):
            continue
        names.setdefault(sub, text)
    return names


def logo_name(host: str) -> str | None:
    """사이트 머리말 로고의 대체 문구에서 기관 이름을 얻는다.

    바닥글 로고·배너 이미지는 학교 공통 문구라 기관 이름이 아니다. 머리말 영역만 본다.
    """
    html = fetch(f"https://{host}/")
    if not html:
        return None

    # 바닥글 앞부분만 대상으로 한다.
    cut = len(html)
    for marker in ("</header>", 'class="foot', "<footer"):
        found = html.find(marker)
        if found > 0:
            cut = min(cut, found)
    head = html[:cut]

    for alt in _LOGO_ALT.findall(head):
        text = re.sub(r"\s+", " ", alt).strip()
        if not text or _ALT_REJECT.search(text):
            continue
        looks_like_logo = re.search(r"로고|logo", text, re.IGNORECASE)
        looks_like_org = text.startswith(("경희대학교 ", "경희대 ")) and len(text) > 6
        if not (looks_like_logo or looks_like_org):
            continue
        name = _CLEAN_NAME.sub("", text).strip()
        name = re.sub(r"^경희대학교\s*", "", name).strip()
        name = re.sub(r"^경희대\s*", "", name).strip()
        if name and name not in ("", "경희대", "KYUNG HEE UNIVERSITY", "경희대학교"):
            return name
    return None


def org_type_for(name: str, host: str) -> str:
    if host == "www.khu.ac.kr":
        return "office"
    if re.search(r"대학원", name):
        return "institute"
    if re.search(r"대학$|칼리지", name):
        return "college"
    if re.search(r"학과|학부|전공", name):
        return "department"
    if re.search(r"센터|처|팀|단|원$|관$|실$", name):
        return "office"
    return "institute"


def build(discovered: dict, *, sleep: float = 0.5) -> tuple[dict, dict]:
    site_names = main_site_names()

    organizations: list[dict] = []
    sources: list[dict] = []
    review_needed: list[str] = []

    for finding in discovered["findings"]:
        host = finding["host"]
        usable = [b for b in finding["boards"] if b.get("usable")]
        if not usable:
            continue

        sub = host.split(".")[0]
        if host == "www.khu.ac.kr":
            org_key = "khu-headquarters"
            org_name = "대학본부"
            needs_review = False
        else:
            name = site_names.get(sub)
            if not name:
                time.sleep(sleep)
                name = logo_name(host)
            needs_review = name is None
            org_name = name or f"[확인 필요] {host}"
            org_key = f"org-{sub}"
            if needs_review:
                review_needed.append(host)

        organizations.append(
            {
                "key": org_key,
                "name": org_name,
                "type": org_type_for(org_name, host),
                "parent": None,
                "campus_codes": [],
                "homepage_url": f"https://{host}/",
                "needs_review": needs_review,
                "evidence_url": MAIN_URL if sub in site_names else f"https://{host}/",
            }
        )

        for board in usable:
            label = (board.get("label") or "").strip()
            if not label:
                continue
            if NOT_NOTICE_LABEL.search(label):
                continue
            if host == "www.khu.ac.kr":
                if not HQ_ALLOW.search(label):
                    continue
            elif not NOTICE_LABEL.search(label):
                continue

            key = f"{sub}-{board['board_code']}-{board['menu_no']}"
            sources.append(
                {
                    "key": key,
                    "name": f"{org_name} {label}".strip(),
                    "organization": org_key,
                    "adapter": "khu_board",
                    "medium": "web",
                    "content_kind": "notice",
                    "interval_minutes": 60,
                    "status": "pending",
                    "config": {
                        "base_url": f"https://{host}",
                        "prefix": board["prefix"],
                        "board_code": board["board_code"],
                        "menu_no": board["menu_no"],
                    },
                    "audiences": [{"type": "organization", "organization": org_key}],
                    "official_evidence_url": board["url"],
                    "notes": f"{discovered['observed_at']} 조사에서 목록 {board['row_count']}건 확인",
                }
            )

    organizations_doc = {
        "meta": {
            "university": {"code": "khu", "name": "경희대학교"},
            "observed_at": discovered["observed_at"],
            "needs_review_hosts": sorted(set(review_needed)),
            "note": "조직 이름은 본부 첫 화면 링크 또는 각 사이트 로고 대체 문구에서 가져왔다. "
            "찾지 못한 곳은 needs_review 로 두고 이름을 지어내지 않았다.",
        },
        "campuses": [
            {"code": CAMPUS_SEOUL, "name": "서울캠퍼스"},
            {"code": CAMPUS_GLOBAL, "name": "국제캠퍼스"},
        ],
        "organizations": organizations,
    }

    sources_doc = {
        "meta": {
            "observed_at": discovered["observed_at"],
            "total": len(sources),
            "note": "status=pending 은 아직 검증하지 않은 후보다. 대표 표본만 active 로 승격한다. "
            "대상 범위 기본값은 출처의 소속 조직이며, 본부 게시판을 전교생 대상으로 단정하지 않는다.",
        },
        "sources": sources,
    }
    return organizations_doc, sources_doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="조사 결과를 등록부로 변환")
    parser.add_argument("--discovered", default=str(BOOTSTRAP / "discovered.json"))
    parser.add_argument("--sleep", type=float, default=0.5)
    args = parser.parse_args(argv)

    path = Path(args.discovered)
    if not path.exists():
        print(f"조사 결과가 없습니다: {path}. 먼저 scripts/discover_sources.py 를 실행하세요.", file=sys.stderr)
        return 1

    discovered = json.loads(path.read_text(encoding="utf-8"))
    organizations_doc, sources_doc = build(discovered, sleep=args.sleep)

    BOOTSTRAP.mkdir(parents=True, exist_ok=True)
    (BOOTSTRAP / "organizations.json").write_text(
        json.dumps(organizations_doc, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (BOOTSTRAP / "sources.json").write_text(
        json.dumps(sources_doc, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        f"organizations: {len(organizations_doc['organizations'])} "
        f"(이름 확인 필요 {len(organizations_doc['meta']['needs_review_hosts'])}) / "
        f"sources: {len(sources_doc['sources'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
