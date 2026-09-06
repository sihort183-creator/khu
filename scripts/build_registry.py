"""조사 결과를 출처 등록부로 바꾼다.

입력:
  registry/bootstrap/board_profiles.json  게시판 성격 판정(공지/소식/자료/…)
  registry/bootstrap/discovered.json      게시판 주소와 사이트 ID
  registry/bootstrap/sites.json           학교가 공개한 사이트 명부(조직 이름의 정본)
  registry/bootstrap/hosts.json           호스트별 게시판 엔진과 페이지 제목

만드는 파일:
  registry/bootstrap/organizations.json   대학·캠퍼스·조직
  registry/bootstrap/sources.json         수집 출처

원칙(5절):
- 조직 이름은 공식 원문에서만 가져온다. 못 찾으면 지어내지 않고 needs_review 로 남긴다.
- 어떤 게시판이 공지인지는 이름이 아니라 `board_profiles.json` 의 판정을 따른다.
  이름으로 고르면 국제처의 `Outgoing Program` 같은 공지를 놓친다.
- 대상 범위 기본값은 출처의 소속 조직이다. 본부 게시판을 전교생 대상으로 단정하지 않는다(4절 4항).

사용: python scripts/build_registry.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO_ROOT / "registry" / "bootstrap"

CAMPUS_SEOUL = "seoul"
CAMPUS_GLOBAL = "global"
CAMPUSES = [{"code": CAMPUS_SEOUL, "name": "서울캠퍼스"}, {"code": CAMPUS_GLOBAL, "name": "국제캠퍼스"}]

# 사이트 명부의 갈래 -> 조직 종류. 계약 어휘(ORG_TYPE_LABELS)에 있는 코드만 쓴다.
SITE_KIND_TO_ORG_TYPE = {
    "COLLEGE": "college",
    "GRADUATE": "college",
    "ATTACHED": "institute",
    "ETC": "office",
}

# 이름이 학과·학부로 끝나면 단과대학이 아니라 학과로 본다.
_DEPARTMENT_NAME = re.compile(r"(학과|학부|전공|계열)\s*$")
_OFFICE_NAME = re.compile(r"(처|팀|본부|센터|지원단|사업단|위원회|대학본부)\s*$")

# 사이트 이름에 섞여 들어오는 표시용 군더더기.
_NAME_NOISE = re.compile(r"</?br\s*/?>", re.IGNORECASE)
_NAME_SUFFIX = re.compile(r"\s*\((국문|영문|중문|일문|한글|영어)\)\s*$")
_TITLE_PREFIX = re.compile(r"^경희대학교\s*|^경희대\s*")
# 사이트 이름에 붙은 개편 연도와 상태 표시. 조직 이름이 아니다.
# 예: `컴퓨터공학부25` -> `컴퓨터공학부`, `디지털콘텐츠학과 (개발중)` -> `디지털콘텐츠학과`.
# 숫자를 무조건 떼면 `커뮤니케이션21` 같은 진짜 이름이 망가지므로 학부·학과·대학 뒤만 뗀다.
_SITE_VERSION = re.compile(r"(학부|학과|대학원|대학)\s*\d{2}\s*$")
_SITE_STATE = re.compile(r"\s*\((개발중|준비중|임시|테스트)\)\s*$")
# 이름 뒤에 건물·호실 주소를 덧붙여 둔 사이트가 있다.
# 예: `건강센터(서울) (경영대학 오비스홀 152호)` -> `건강센터(서울)`.
_NAME_ADDRESS = re.compile(r"\s*\([^()]*(관|홀|동|호|층|캠퍼스\s*내)\s*\)\s*$")

# 주소에서 캠퍼스를 알아낸다. 확실할 때만 붙이고 아니면 비워 둔다.
_SEOUL_ADDR = re.compile(r"서울|동대문|회기|청운")
_GLOBAL_ADDR = re.compile(r"용인|기흥|국제캠퍼스|덕영대로")


def read(name: str) -> dict:
    path = BOOTSTRAP / name
    if not path.exists():
        raise SystemExit(f"{path} 가 없습니다. 조사 스크립트를 먼저 실행하세요.")
    return json.loads(path.read_text(encoding="utf-8"))


def clean_name(raw: str | None) -> str:
    if not raw:
        return ""
    value = _NAME_NOISE.sub(" ", raw)
    value = re.sub(r"\s+", " ", value).strip()
    value = _NAME_SUFFIX.sub("", value).strip()
    value = _SITE_STATE.sub("", value).strip()
    value = _SITE_VERSION.sub(lambda m: m.group(1), value).strip()
    value = _NAME_ADDRESS.sub("", value).strip()
    return value


def org_type_for(name: str, site_kind: str | None) -> str:
    """조직 종류를 정한다. 이름이 더 구체적이면 이름을 따른다."""
    if _DEPARTMENT_NAME.search(name):
        return "department"
    if _OFFICE_NAME.search(name):
        return "office"
    return SITE_KIND_TO_ORG_TYPE.get(site_kind or "", "institute")


def campus_codes_for(site: dict | None) -> list[str]:
    if not site:
        return []
    text = f"{site.get('addr') or ''} {site.get('siteNm') or ''}"
    codes = []
    if _SEOUL_ADDR.search(text):
        codes.append(CAMPUS_SEOUL)
    if _GLOBAL_ADDR.search(text):
        codes.append(CAMPUS_GLOBAL)
    return codes


def build(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="출처 등록부 생성")
    parser.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 요약만 낸다")
    args = parser.parse_args(argv)

    profiles = read("board_profiles.json")
    discovered = read("discovered.json")
    sites_doc = read("sites.json")
    hosts_doc = read("hosts.json")

    sites_by_id = {s["siteId"]: s for s in sites_doc["sites"]}
    host_title = {h["host"]: h.get("title") for h in hosts_doc["hosts"]}
    host_adapter = {f["host"]: f.get("adapter", "khu_board") for f in discovered["findings"]}

    # 게시판 주소 -> 사이트 ID. 같은 호스트에 다른 사이트의 게시판이 걸려 있기도 해서
    # 조직은 호스트가 아니라 사이트 ID 로 정한다.
    prefix_of: dict[str, str] = {}
    for finding in discovered["findings"]:
        for board in finding["boards"]:
            prefix_of[board["url"]] = board.get("prefix") or ""

    notices = [b for b in profiles["boards"] if b["kind"] == "notice"]

    organizations: dict[str, dict] = {}
    sources: list[dict] = []
    unnamed: list[str] = []

    def organization_for(board: dict) -> str:
        """게시판이 속한 조직을 찾고, 없으면 만든다."""
        host = board["host"]
        site_id = prefix_of.get(board["url"], "")
        site = sites_by_id.get(site_id)

        if site and clean_name(site.get("siteNm")):
            key = f"org-{site_id}"
            name = clean_name(site["siteNm"])
            evidence = site.get("siteDomain") or f"https://{host}/"
            needs_review = False
        else:
            # 명부에 없는 사이트다. 페이지 제목이 유일한 공식 표기다.
            key = f"org-{host.split('.')[0]}"
            title = clean_name(host_title.get(host))
            name = _TITLE_PREFIX.sub("", title).strip() if title else ""
            evidence = f"https://{host}/"
            needs_review = not name
            if needs_review:
                name = f"[확인 필요] {host}"
                if host not in unnamed:
                    unnamed.append(host)

        if key not in organizations:
            organizations[key] = {
                "key": key,
                "name": name,
                "type": org_type_for(name, site.get("siteCd") if site else None),
                "parent": None,
                "campus_codes": campus_codes_for(site),
                "homepage_url": f"https://{host}/",
                "needs_review": needs_review,
                "evidence_url": evidence,
            }
        return key

    for board in notices:
        host = board["host"]
        adapter = board.get("adapter") or host_adapter.get(host, "khu_board")
        org_key = organization_for(board)
        label = clean_name(board.get("label")) or "공지"

        if adapter == "gnuboard":
            key = f"{host.split('.')[0]}-{board['board_code']}"
            config = {"base_url": f"https://{host}", "bo_table": board["board_code"]}
        else:
            site_id = prefix_of.get(board["url"], "") or host.split(".")[0]
            key = f"{site_id}-{board['board_code']}-{board['menu_no']}"
            config = {
                "base_url": f"https://{host}",
                "prefix": site_id,
                "board_code": board["board_code"],
                "menu_no": board["menu_no"],
            }

        org_name = organizations[org_key]["name"]
        name = label if label.startswith(org_name) else f"{org_name} {label}"

        sources.append(
            {
                "key": key,
                "name": name.strip(),
                "organization": org_key,
                "adapter": adapter,
                "medium": "web",
                "content_kind": "notice",
                "interval_minutes": 60,
                # 판정 근거가 뚜렷한 것만 바로 수집하고, 나머지는 운영자가 승격한다.
                "status": "active" if board.get("confidence") == "high" else "pending",
                "config": config,
                "audiences": [{"type": "organization", "organization": org_key}],
                "official_evidence_url": board["url"],
                "notes": (
                    f"{profiles['observed_at']} 조사: {board.get('reason', '')}"
                    f" (메뉴 경로: {' > '.join(board.get('menu_path', [])) or '없음'})"
                ),
            }
        )

    # 같은 게시판이 여러 호스트에서 그대로 서비스된다. 예를 들어 호텔관광대학의
    # 게시판(hot20_kor)은 10개 학과 사이트에서 같은 내용으로 열린다. 합치지 않으면
    # 같은 공지를 열 번 수집한다. 사이트 명부가 가리키는 원래 도메인을 대표로 삼는다.
    def preference(source: dict) -> tuple[int, str]:
        site = sites_by_id.get(source["config"].get("prefix", ""))
        home = (site.get("siteDomain") or "") if site else ""
        own_host = home.split("//")[-1].strip("/").lower() if "//" in home else ""
        base_host = source["config"]["base_url"].split("//")[-1]
        return (0 if own_host and own_host == base_host else 1, base_host)

    unique: dict[str, dict] = {}
    for source in sorted(sources, key=preference):
        unique.setdefault(source["key"], source)
    duplicates = len(sources) - len(unique)
    sources = sorted(unique.values(), key=lambda s: s["key"])

    orgs_doc = {
        "meta": {
            "university": {"code": "khu", "name": "경희대학교"},
            "observed_at": profiles["observed_at"],
            "needs_review_hosts": sorted(unnamed),
            "note": (
                "조직 이름은 학교가 공개한 사이트 명부(sites.json)의 사이트 이름에서 가져왔다."
                " 명부에 없는 사이트는 페이지 제목을 썼고, 그마저 없으면 needs_review 로 두고"
                " 이름을 지어내지 않았다. 캠퍼스는 명부의 주소로 확실할 때만 붙였다."
            ),
        },
        "campuses": CAMPUSES,
        "organizations": sorted(organizations.values(), key=lambda o: o["key"]),
    }

    status_counts = Counter(s["status"] for s in sources)
    sources_doc = {
        "meta": {
            "observed_at": profiles["observed_at"],
            "total": len(sources),
            "merged_mirrors": duplicates,
            "status_counts": dict(status_counts),
            "note": (
                "공지 여부는 게시판 이름이 아니라 board_profiles.json 의 판정을 따른다."
                " 판정 근거가 뚜렷한 것(confidence=high)만 active 로 두고 나머지는 pending 이다."
                " 대상 범위 기본값은 출처의 소속 조직이며, 본부 게시판을 전교생 대상으로 단정하지 않는다."
                " 게시판 성격은 학기마다 다시 조사한다."
            ),
        },
        "sources": sources,
    }

    print(f"조직 {len(organizations)}개 (이름 못 찾음 {len(unnamed)}곳)")
    print(f"여러 호스트에 같은 게시판이 걸려 합친 것 {duplicates}개")
    print(f"출처 {len(sources)}개 {dict(status_counts)}")
    print("어댑터:", dict(Counter(s["adapter"] for s in sources)))
    print("조직 종류:", dict(Counter(o["type"] for o in organizations.values())))

    if args.dry_run:
        return 0

    (BOOTSTRAP / "organizations.json").write_text(
        json.dumps(orgs_doc, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (BOOTSTRAP / "sources.json").write_text(
        json.dumps(sources_doc, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"-> {BOOTSTRAP / 'organizations.json'}")
    print(f"-> {BOOTSTRAP / 'sources.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
