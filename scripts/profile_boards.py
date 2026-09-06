"""게시판 성격 조사(0단계 조사용).

게시판 이름만 보고 공지 여부를 정하면 틀린다. 실제로 oia 의 `Outgoing Program`
게시판에는 마감이 있는 교환학생 모집 공고가 올라오고, 같은 사이트의 `News` 에는
행사 후기만 올라온다. 이름이 아니라 다음 세 가지로 판정한다.

  1. board_code  — 사람·영상 목록처럼 공지가 올라올 수 없는 유형을 먼저 뺀다.
  2. 상위 메뉴   — 게시판이 `Announcement` 아래 묶여 있으면 이름과 무관하게 공지 묶음이다.
  3. 최근 글     — 제목·날짜·작성자로 실제로 무엇이 올라오는지 본다.

게시판 성격은 학기 중에 바뀌지 않는다. **재조사는 학기마다 한 번이면 충분하다.**
결과 파일에 조사 시점을 남기므로, 다음 학기 시작 때 다시 돌려 갱신한다.

사용:
    python scripts/profile_boards.py --out registry/bootstrap/board_profiles.json
    python scripts/profile_boards.py --hosts oia.khu.ac.kr --out -

이 도구는 조사 결과를 만들 뿐이고 운영 데이터베이스에 아무것도 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from lxml import html as lxml_html

REPO_ROOT = Path(__file__).resolve().parents[1]
UA = "khu-notice-bot/0.1 (+https://github.com/sihort183-creator/khu)"
DISCOVERED_PATH = REPO_ROOT / "registry" / "bootstrap" / "discovered.json"

# 공지가 올라올 수 없는 게시판 종류. 2026-09-06 표본으로 확인했다.
#   BMSR00047 교수·연구원 명단   BMSR00059 연구실 소개
#   BMSR00046 홍보 영상          BMSR00060 교수진 명단
STRUCTURAL_NON_NOTICE = {
    "BMSR00047": "사람 명단(교수·연구원)",
    "BMSR00059": "연구실·동아리 소개",
    "BMSR00046": "영상 모음",
    "BMSR00060": "사람 명단(교수진)",
}

_VIEW_CALL = re.compile(r"view\(\s*'([^']*)'")
_DATE_TEXT = re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})")

# 상위 메뉴가 이 낱말이면 이름과 상관없이 공지 묶음으로 본다.
# oia 의 `Outgoing Program` 은 이름에 공지가 없지만 `Announcement` 아래 묶여 있다.
_NOTICE_MENU = re.compile(r"공지|알림|announcement|notice|학사안내|소식·?공지", re.IGNORECASE)

# 이 낱말은 공지도 잡동사니도 담는 그릇이라 상위 메뉴만으로는 판단하지 않는다.
# 실제로 커뮤니티 아래에 공지사항이 들어 있는 곳이 많아 버리지도 않는다.
_MIXED_MENU = re.compile(r"커뮤니티|community|게시판|board|자료실|정보마당", re.IGNORECASE)

# 글 제목에 나타나는 성격 신호.
_ACTION_WORDS = re.compile(
    r"모집|신청|접수|선발|공고|안내|시행|제출|마감|공모|참가|지원자|대상자|일정|변경|"
    r"등록|납부|수강|신청서|설명회|면접|합격|추천|채용|공채|리크루팅|인턴|장학|특강|"
    r"세미나|콜로키움|워크숍|워크샵|시험|평가|휴학|복학|졸업|학위|배정|선정|안내문|"
    r"apply|application|recruit|deadline|scholarship|seminar|colloquium",
    re.IGNORECASE,
)
# 공지라고 단정할 만큼 뚜렷한 낱말. "안내"처럼 두루 쓰이는 말은 넣지 않는다.
_STRONG_ACTION = re.compile(
    r"모집|신청|접수|선발|공고|마감|제출|공모|채용|공채|리크루팅|납부|수강|휴학|복학|"
    r"시험|평가|설명회|면접|합격|추천|장학|인턴|apply|application|recruit|deadline",
    re.IGNORECASE,
)
_NEWS_WORDS = re.compile(
    r"시상|수상|협약|체결|후기|성료|성과|개최\s*결과|참여했|방문했|인터뷰|기사|보도|화보|"
    r"스케치|뉴스|칼럼|기고|축사|동정|다녀왔|열렸|진행했|선정되|임명|취임|부고|추모",
    re.IGNORECASE,
)
_ARCHIVE_WORDS = re.compile(
    r"백서|책자|자료집|양식|서식|규정|시행세칙|교육과정|브로슈어|brochure|매뉴얼|가이드북|"
    r"학회지|논문집|연보|실적|수업\s*자료|강의\s*자료|참고자료",
    re.IGNORECASE,
)

# 게시판 자체가 공지가 아닌 종류임을 이름·메뉴 경로로 알 수 있는 경우.
# 표본을 눈으로 확인해 정리했다(2026-09-06).
_GALLERY_MENU = re.compile(r"갤러리|사진|포토|photo|gallery|화보|앨범", re.IGNORECASE)
_STATIC_MENU = re.compile(
    r"교육과정|연보|curriculum|연혁|조직도|시설안내|약도|오시는\s*길|연구실\s*소개|"
    r"구성원|명단|위원회|계열|자료실|강의자료",
    re.IGNORECASE,
)
_NEWS_MENU = re.compile(
    r"학과소식|동문소식|기자단|pr\s*room|보도자료|언론|홍보|news|소식지|웹진|webzine",
    re.IGNORECASE,
)
# 연구 성과·명단처럼 사람에게 알릴 일이 아니라 쌓아 두는 기록.
_RECORD_MENU = re.compile(
    r"성과\s*관리|우수성과|research\s*highlights|논문|저서|특허|연구실적|수상실적|"
    r"참여\s*대학원생|명단|현황|협동과정|전공\s*및\s*연구실|결산|발전기금",
    re.IGNORECASE,
)
# 이름 자체가 "쌓아 두는 자료"인 게시판. 행사·공지 규칙보다 먼저 본다.
# 학술대회 게시판 아래의 `발표 자료`, 공지 묶음 아래의 `기출 문제`가 여기 걸린다.
_ARCHIVE_MENU = re.compile(
    r"자료실|자료\s*모음|발표\s*자료|강의\s*자료|수업\s*자료|기출|서식|양식|규정|"
    r"강의계획|책자|브로슈어|brochure|아카이브|archive|다운로드|download",
    re.IGNORECASE,
)
# 앞으로 열리는 행사 안내는 공지다. 지나간 후기와 구분한다.
_EVENT_MENU = re.compile(
    r"콜로키움|colloquium|학술행사|세미나|seminar|특강|포럼|forum|공연|전시|학술제",
    re.IGNORECASE,
)
# 동문·재학생 소식 게시판.
_ALUMNI_MENU = re.compile(r"동문회|동문|총동문|학부소식|학과\s*소식|재학생\s*소식", re.IGNORECASE)

# 대학평가 실적 보고. 학생 공지가 아니라 기관 자료다.
_SDG_REPORT = re.compile(r"\[SDG\s*\d+\]|\[R-\d+\]", re.IGNORECASE)


class Fetcher:
    """호스트마다 요청 간격을 지키는 수집기."""

    def __init__(self, delay: float = 0.7, timeout: float = 20.0) -> None:
        self.delay = delay
        self.timeout = timeout
        self._next_allowed: dict[str, float] = {}
        self._lock = threading.Lock()

    def get(self, url: str) -> tuple[int, str]:
        host = urlsplit(url).netloc
        with self._lock:
            now = time.monotonic()
            earliest = max(self._next_allowed.get(host, 0.0), now)
            self._next_allowed[host] = earliest + self.delay
        if earliest > now:
            time.sleep(earliest - now)
        request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, ""
        except Exception:
            return 0, ""


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def menu_path(doc, marker: str) -> list[str]:
    """게시판이 메뉴 어디에 묶여 있는지 거슬러 올라간다.

    `Outgoing Program` 이 `Announcement` 아래 있다는 사실이 이름보다 중요하다.
    같은 메뉴가 상단·좌측에 여러 번 나오므로 가장 깊은 경로를 고른다.
    marker 는 CMS 면 `menuNo=...`, 그누보드면 `bo_table=...` 이다.
    """
    best: list[str] = []
    for anchor in doc.xpath(f"//a[contains(@href,'{marker}')]"):
        chain: list[str] = []
        node = anchor
        for _ in range(10):
            node = node.getparent()
            if node is None:
                break
            if node.tag != "li":
                continue
            own = node.xpath("./a|./*/a")
            label = _clean(own[0].text_content()) if own else ""
            if label and 1 < len(label) <= 40 and label not in chain:
                chain.append(label)
        if len(chain) > len(best):
            best = chain
    return list(reversed(best))


def list_rows(doc, adapter: str = "khu_board", bo_table: str = "") -> list[dict]:
    """목록 한 쪽에서 글의 제목·날짜를 뽑는다."""
    if adapter == "gnuboard":
        # 다른 게시판을 끌어온 홍보 영역이 섞이므로 bo_table 이 맞는 링크만 본다.
        cond = f"contains(@href,'bo_table={bo_table}&') and contains(@href,'wr_id=')"
        link_xpath = f".//a[{cond}]"
        containers = doc.xpath(
            f"//tr[.//a[{cond}]]"
            f" | //div[contains(@class,'div_tb_tr')][.//a[{cond}]]"
            f" | //li[contains(@class,'item')][.//a[{cond}]]"
        )
    else:
        link_xpath = ".//a[contains(@href,'view(')]"
        containers = doc.xpath("//tr[.//a[contains(@href,'view(')]]") or doc.xpath(
            "//li[.//a[contains(@href,'view(')]]"
        )

    items: list[dict] = []
    for node in containers:
        link = node.xpath(link_xpath)
        if not link:
            continue
        if adapter != "gnuboard" and not _VIEW_CALL.search(link[0].get("href") or ""):
            continue
        text = _clean(node.text_content())
        title = _clean(link[0].text_content())
        if not title:
            continue
        found = _DATE_TEXT.search(text)
        published = None
        if found:
            year, month, day = (int(g) for g in found.groups())
            if 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                published = f"{year:04d}-{month:02d}-{day:02d}"
        items.append({"title": title[:120], "published": published})
    return items


def classify(board: dict, path: list[str], items: list[dict], today: datetime) -> dict:
    """게시판 성격을 정한다. 판단 근거를 함께 남긴다."""
    code = board["board_code"]
    if board.get("_adapter", "khu_board") == "khu_board" and code in STRUCTURAL_NON_NOTICE:
        return {"kind": "excluded", "reason": f"게시판 종류: {STRUCTURAL_NON_NOTICE[code]}", "confidence": "high"}

    dated = [i["published"] for i in items if i["published"]]
    latest = max(dated) if dated else None
    if not items:
        return {"kind": "empty", "reason": "글이 없다.", "confidence": "high"}

    recent = 0
    if dated:
        cutoff = (today - timedelta(days=365)).strftime("%Y-%m-%d")
        recent = sum(1 for d in dated if d >= cutoff)

    titles = " \n".join(i["title"] for i in items)
    action = len(_ACTION_WORDS.findall(titles))
    news = len(_NEWS_WORDS.findall(titles))
    archive = len(_ARCHIVE_WORDS.findall(titles))

    # 상위 메뉴가 공지 묶음이면 게시판 이름이 무엇이든 공지로 본다.
    in_notice_menu = any(_NOTICE_MENU.search(p) for p in path)
    # 커뮤니티·자료실처럼 무엇이든 담는 메뉴는 그 자체로는 근거가 못 된다.
    in_mixed_menu = any(_MIXED_MENU.search(p) for p in path)

    signals = {
        "items": len(items),
        "action": action,
        "news": news,
        "archive": archive,
        "recent_1y": recent,
        "latest": latest,
        "in_notice_menu": in_notice_menu,
        "in_mixed_menu": in_mixed_menu,
    }

    if latest and latest < (today - timedelta(days=730)).strftime("%Y-%m-%d"):
        return {
            "kind": "dormant",
            "reason": f"마지막 글이 {latest} 로 2년 넘게 멈춰 있다.",
            "confidence": "high",
            "signals": signals,
        }

    where = f"{board.get('label') or ''} {' > '.join(path)}"
    titles_and_where = f"{titles} {where}"
    if _SDG_REPORT.search(titles_and_where):
        return {
            "kind": "excluded",
            "reason": "대학평가 실적 보고(SDG)라 학생 공지가 아니다.",
            "confidence": "high",
            "signals": signals,
        }
    if _GALLERY_MENU.search(where):
        return {"kind": "news", "reason": "사진 갤러리다.", "confidence": "high", "signals": signals}
    if _NEWS_MENU.search(where) or _ALUMNI_MENU.search(where):
        return {"kind": "news", "reason": "학과·기관 소식 게시판이다.", "confidence": "high", "signals": signals}
    if _RECORD_MENU.search(where):
        return {
            "kind": "archive",
            "reason": "연구 성과·명단 같은 기록이라 알릴 공지가 아니다.",
            "confidence": "medium",
            "signals": signals,
        }
    if _ARCHIVE_MENU.search(where):
        return {
            "kind": "archive",
            "reason": "자료·서식을 쌓아 두는 게시판이다.",
            "confidence": "high",
            "signals": signals,
        }
    if _EVENT_MENU.search(where):
        return {
            "kind": "notice",
            "reason": "앞으로 열리는 행사를 알리는 게시판이다.",
            "confidence": "medium",
            "signals": signals,
        }
    if _STATIC_MENU.search(where) and action < max(2, len(items) // 3):
        return {
            "kind": "archive",
            "reason": "교육과정·자료실 같은 정적 안내다.",
            "confidence": "medium",
            "signals": signals,
        }

    # 상위 메뉴가 공지 묶음이면 그것만으로 충분하다. oia 의 Outgoing Program 이 여기 걸린다.
    if in_notice_menu:
        return {
            "kind": "notice",
            "reason": f"상위 메뉴가 공지 묶음이다({' > '.join(path)}).",
            "confidence": "high" if action >= 2 else "medium",
            "signals": signals,
        }

    third = max(2, len(items) // 3)
    # 제목의 "안내"는 자료 게시판에도 흔해서 그것만으로 공지로 보지 않는다.
    weak_only = action > 0 and not _STRONG_ACTION.search(titles)
    if weak_only and archive == 0 and news == 0:
        return {
            "kind": "unclear",
            "reason": "'안내' 말고는 신호가 없다. 표본을 더 봐야 한다.",
            "confidence": "low",
            "signals": signals,
        }
    if archive >= third and archive > action:
        return {"kind": "archive", "reason": "자료·서식 위주다.", "confidence": "medium", "signals": signals}
    if news >= third and news > action:
        return {"kind": "news", "reason": "지나간 소식 위주다.", "confidence": "medium", "signals": signals}
    if action >= third and action >= 2:
        return {
            "kind": "notice",
            "reason": "모집·신청·마감 글이 꾸준히 올라온다.",
            "confidence": "high",
            "signals": signals,
        }

    # 커뮤니티·자료실 아래인데 신호가 약한 것들이 여기 모인다. 버리지 않고 따로 본다.
    return {
        "kind": "unclear",
        "reason": (
            "신호가 약하다. 커뮤니티·자료실 계열이라 공지가 섞여 있을 수 있어 표본을 더 봐야 한다."
            if in_mixed_menu
            else "신호가 약하다. 표본을 더 봐야 한다."
        ),
        "confidence": "low",
        "signals": signals,
    }


def profile_board(board: dict, host: str, fetcher: Fetcher, today: datetime,
                  adapter: str = "khu_board") -> dict:
    record = {
        "host": host,
        "adapter": adapter,
        "board_code": board["board_code"],
        "menu_no": board["menu_no"],
        "url": board["url"],
        "label": board.get("label"),
    }
    if adapter == "khu_board" and board["board_code"] in STRUCTURAL_NON_NOTICE:
        # 원문을 열지 않아도 되는 유형이다. 요청을 아낀다.
        record.update(menu_path=[], sample=[], **classify({**board, "_adapter": adapter}, [], [], today))
        return record

    status, text = fetcher.get(board["url"])
    if status != 200 or not text:
        record.update(
            menu_path=[], sample=[], kind="unreachable", reason=f"목록을 열지 못했다(status={status}).",
            confidence="high",
        )
        return record

    doc = lxml_html.fromstring(text)
    if adapter == "gnuboard":
        marker = f"bo_table={board['board_code']}"
        items = list_rows(doc, adapter, board["board_code"])
    else:
        marker = f"menuNo={board['menu_no']}"
        items = list_rows(doc, adapter)
    path = menu_path(doc, marker)
    record.update(menu_path=path, sample=items[:10], item_count=len(items))
    record.update(classify({**board, "_adapter": adapter}, path, items, today))
    return record


def reclassify(path: str, out: str | None) -> int:
    """저장해 둔 표본으로 판정만 다시 한다.

    규칙을 고칠 때마다 원문을 다시 받으면 통신량이 크다. 표본은 이미 파일에 있으므로
    판정만 새로 하면 요청이 한 번도 나가지 않는다.
    """
    source = Path(path)
    if not source.is_absolute():
        source = REPO_ROOT / source
    doc = json.loads(source.read_text(encoding="utf-8"))
    today = datetime.now(UTC)

    counts: dict[str, int] = {}
    for record in doc["boards"]:
        if record["kind"] == "unreachable":
            counts[record["kind"]] = counts.get(record["kind"], 0) + 1
            continue
        board = {
            "board_code": record["board_code"],
            "menu_no": record.get("menu_no", ""),
            "label": record.get("label"),
            "_adapter": record.get("adapter", "khu_board"),
        }
        verdict = classify(board, record.get("menu_path", []), record.get("sample", []), today)
        record.update(verdict)
        counts[record["kind"]] = counts.get(record["kind"], 0) + 1

    doc["reclassified_at"] = today.strftime("%Y-%m-%dT%H:%M:%SZ")
    doc["kind_counts"] = counts

    target = Path(out) if out and out != "-" else source
    if not target.is_absolute():
        target = REPO_ROOT / target
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"재판정 {len(doc['boards'])}개 {counts} -> {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="경희대 게시판 성격 조사")
    parser.add_argument("--out", default=None, help="결과를 쓸 JSON 경로('-' 이면 화면)")
    parser.add_argument("--hosts", nargs="*", default=None, help="조사할 도메인(기본: 전체)")
    parser.add_argument("--delay", type=float, default=0.7, help="같은 호스트 요청 간격(초)")
    parser.add_argument("--workers", type=int, default=10, help="동시에 조사할 호스트 수")
    parser.add_argument(
        "--reclassify",
        default=None,
        help="이미 받아 둔 조사 결과의 표본으로 판정만 다시 한다(원문을 받지 않는다).",
    )
    args = parser.parse_args(argv)

    if args.reclassify:
        return reclassify(args.reclassify, args.out)

    discovered = json.loads(DISCOVERED_PATH.read_text(encoding="utf-8"))
    today = datetime.now(UTC)
    fetcher = Fetcher(delay=args.delay)

    by_host: dict[str, list[dict]] = {}
    host_adapter: dict[str, str] = {}
    for finding in discovered["findings"]:
        if args.hosts and finding["host"] not in args.hosts:
            continue
        host_adapter[finding["host"]] = finding.get("adapter", "khu_board")
        for board in finding["boards"]:
            if board["usable"]:
                by_host.setdefault(finding["host"], []).append(board)

    def run(host: str) -> list[dict]:
        adapter = host_adapter.get(host, "khu_board")
        out = [profile_board(b, host, fetcher, today, adapter) for b in by_host[host]]
        kinds = {}
        for record in out:
            kinds[record["kind"]] = kinds.get(record["kind"], 0) + 1
        print(f"{host}: {len(out)}개 {kinds}", flush=True)
        return out

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = [r for group in pool.map(run, sorted(by_host)) for r in group]

    counts: dict[str, int] = {}
    for record in results:
        counts[record["kind"]] = counts.get(record["kind"], 0) + 1

    payload = {
        "observed_at": today.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "revalidate": "학기마다 한 번. 게시판 성격은 학기 중에 바뀌지 않는다.",
        "method": "board_code(구조) → 상위 메뉴 → 최근 글 제목. 게시판 이름은 판단에 쓰지 않는다.",
        "board_count": len(results),
        "kind_counts": counts,
        "boards": results,
    }
    if args.out and args.out != "-":
        target = Path(args.out)
        if not target.is_absolute():
            target = REPO_ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(results)}개 조사 {counts} -> {target}")
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
