"""경희대 게시판 탐색 도구(0단계 조사용).

`registry/bootstrap/sites.json` 의 공식 사이트 명부를 출발점으로, 각 사이트의 메뉴를
따라가며 게시판을 빠짐없이 찾는다. 첫 화면에 걸린 링크만 보던 예전 방식은 메뉴 안쪽
게시판을 통째로 놓쳤기 때문에, 이제 같은 호스트 안에서 메뉴 페이지를 너비 우선으로
훑는다.

사용:
    python scripts/discover_sites.py --out registry/bootstrap/sites.json
    python scripts/discover_sources.py --out registry/bootstrap/discovered.json
    python scripts/discover_sources.py --hosts swcon.khu.ac.kr --depth 3

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
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
UA = "khu-notice-bot/0.1 (+https://github.com/sihort183-creator/khu)"
SITES_PATH = REPO_ROOT / "registry" / "bootstrap" / "sites.json"
HOSTS_PATH = REPO_ROOT / "registry" / "bootstrap" / "hosts.json"

# 경희대 CMS 의 게시판 목록과 일반 내용 페이지. 둘 다 메뉴를 그대로 달고 있어서
# 어느 쪽을 열든 사이트의 나머지 메뉴가 따라온다.
BOARD_LINK = re.compile(r"/([A-Za-z0-9_]+)/user/bbs/([A-Za-z0-9]+)/list\.do\?menuNo=(\d+)")
CONTENT_LINK = re.compile(r"/([A-Za-z0-9_]+)/user/contents/view\.do\?menuNo=(\d+)")
GNU_BOARD = re.compile(r"/bbs/board\.php\?bo_table=([A-Za-z0-9_]+)")
TITLE_TAG = re.compile(r"<title>([^<]*)</title>")
BOARD_TABLE = re.compile(r'<table[^>]*class="[^"]*board01')
# 게시판 한 줄은 view('...') 호출이나 상세 링크로 나타난다.
ROW_CALL = re.compile(r"view\('|/user/bbs/[A-Za-z0-9]+/view\.do")
ERROR_PAGE = re.compile(r"<title>에러안내</title>")


class Fetcher:
    """호스트마다 요청 간격을 지키는, 최소한의 예의를 갖춘 수집기."""

    def __init__(self, delay: float = 1.0, timeout: float = 20.0) -> None:
        self.delay = delay
        self.timeout = timeout
        self._next_allowed: dict[str, float] = {}
        self._lock = threading.Lock()

    def _wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            earliest = max(self._next_allowed.get(host, 0.0), now)
            self._next_allowed[host] = earliest + self.delay
        sleep_for = earliest - now
        if sleep_for > 0:
            time.sleep(sleep_for)

    def get(self, url: str) -> tuple[int, str]:
        self._wait(urlsplit(url).netloc)
        request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, ""
        except Exception:
            return 0, ""


def anchor_labels(html: str) -> dict[str, str]:
    """페이지 안의 링크 주소 -> 표시 이름."""
    labels: dict[str, str] = {}
    for href, inner in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip()
        if text and 1 < len(text) <= 40:
            labels.setdefault(href.replace("&amp;", "&"), text)
    return labels


def crawl_host(host: str, fetcher: Fetcher, *, depth: int = 3, page_budget: int = 80) -> dict:
    """한 호스트의 메뉴를 훑어 게시판 목록을 만든다."""
    base = f"https://{host}"
    result: dict = {"host": host, "home_status": 0, "boards": [], "error": None, "pages_fetched": 0}

    status, home = fetcher.get(base + "/")
    result["home_status"] = status
    if status != 200 or not home:
        result["error"] = "첫 화면을 열지 못했습니다."
        return result

    title = TITLE_TAG.search(home)
    result["title"] = re.sub(r"\s+", " ", title.group(1)).strip() if title else None

    labels: dict[str, str] = {}
    boards: dict[str, dict] = {}
    seen_menus: set[str] = set()
    queue: deque[tuple[str, int]] = deque()
    pages = 1

    def note(html: str, level: int) -> None:
        """페이지에서 찾은 메뉴를 큐에 넣는다."""
        labels.update(anchor_labels(html))
        for prefix, board_code, menu_no in BOARD_LINK.findall(html):
            path = f"/{prefix}/user/bbs/{board_code}/list.do?menuNo={menu_no}"
            if path not in seen_menus:
                seen_menus.add(path)
                queue.append((path, level + 1))
        for prefix, menu_no in CONTENT_LINK.findall(html):
            path = f"/{prefix}/user/contents/view.do?menuNo={menu_no}"
            if path not in seen_menus:
                seen_menus.add(path)
                queue.append((path, level + 1))

    note(home, 0)

    while queue and pages < page_budget:
        path, level = queue.popleft()
        if level > depth:
            continue
        url = base + path
        page_status, html = fetcher.get(url)
        pages += 1
        if page_status == 200 and html and not ERROR_PAGE.search(html):
            note(html, level)

        match = BOARD_LINK.search(path)
        if not match:
            continue
        prefix, board_code, menu_no = match.groups()
        rows = len(ROW_CALL.findall(html)) if html else 0
        boards[path] = {
            "board_code": board_code,
            "menu_no": menu_no,
            "prefix": prefix,
            "url": url,
            "label": labels.get(path) or labels.get(url),
            "status": page_status,
            "depth": level,
            "has_table": bool(html and BOARD_TABLE.search(html)),
            "row_count": rows,
            "usable": bool(page_status == 200 and rows > 0),
        }

    # 라벨은 나중에 열린 페이지에서 발견되기도 한다. 마지막에 한 번 더 맞춰준다.
    for path, board in boards.items():
        if not board["label"]:
            board["label"] = labels.get(path) or labels.get(board["url"])

    result["pages_fetched"] = pages
    result["queue_remaining"] = len(queue)
    result["boards"] = sorted(
        boards.values(), key=lambda b: (b["prefix"], b["board_code"], int(b["menu_no"]))
    )
    return result


def crawl_gnuboard_host(host: str, fetcher: Fetcher, *, page_budget: int = 40) -> dict:
    """그누보드 사이트의 게시판을 훑는다.

    CMS 와 달리 메뉴가 bo_table 이름으로 드러나 있어 첫 화면과 각 게시판 화면에서
    링크를 모으면 대부분 찾을 수 있다.
    """
    base = f"https://{host}"
    result: dict = {
        "host": host, "adapter": "gnuboard", "home_status": 0,
        "boards": [], "error": None, "pages_fetched": 0,
    }
    status, home = fetcher.get(base + "/")
    result["home_status"] = status
    if status != 200 or not home:
        result["error"] = "첫 화면을 열지 못했습니다."
        return result

    title = TITLE_TAG.search(home)
    result["title"] = re.sub(r"\s+", " ", title.group(1)).strip() if title else None

    labels: dict[str, str] = {}
    tables: dict[str, dict] = {}
    queue: deque[str] = deque()
    seen: set[str] = set()

    def note(html: str) -> None:
        for href, inner in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
            found = GNU_BOARD.search(href.replace("&amp;", "&"))
            if not found:
                continue
            table = found.group(1)
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip()
            # 글 링크(wr_id)가 아니라 게시판 링크의 글자만 이름으로 쓴다.
            if text and 1 < len(text) <= 40 and "wr_id=" not in href:
                labels.setdefault(table, text)
            if table not in seen:
                seen.add(table)
                queue.append(table)

    note(home)
    pages = 1
    while queue and pages < page_budget:
        table = queue.popleft()
        url = f"{base}/bbs/board.php?bo_table={table}"
        page_status, html = fetcher.get(url)
        pages += 1
        if page_status == 200 and html:
            note(html)
        rows = len(set(re.findall(rf"bo_table={re.escape(table)}&(?:amp;)?wr_id=(\d+)", html))) if html else 0
        tables[table] = {
            "board_code": table,
            "menu_no": "",
            "prefix": "",
            "url": url,
            "label": labels.get(table),
            "status": page_status,
            "depth": 1,
            "has_table": bool(html and "td_subject" in html),
            "row_count": rows,
            "usable": bool(page_status == 200 and rows > 0),
        }

    result["pages_fetched"] = pages
    result["queue_remaining"] = len(queue)
    result["boards"] = sorted(tables.values(), key=lambda b: b["board_code"])
    return result


def load_hosts() -> list[tuple[str, str]]:
    """수집 대상 호스트와 어댑터를 읽는다."""
    if HOSTS_PATH.exists():
        doc = json.loads(HOSTS_PATH.read_text(encoding="utf-8"))
        return [(r["host"], r["adapter"]) for r in doc["hosts"] if r.get("adapter")]
    if not SITES_PATH.exists():
        raise SystemExit(
            f"{HOSTS_PATH} 도 {SITES_PATH} 도 없습니다. 먼저 "
            "`python scripts/discover_sites.py` 와 `python scripts/discover_hosts.py` 를 실행하세요."
        )
    return [(h, "khu_board") for h in json.loads(SITES_PATH.read_text(encoding="utf-8"))["hosts"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="경희대 게시판 출처 조사")
    parser.add_argument("--hosts", nargs="*", default=None, help="조사할 도메인 목록(기본: 사이트 명부 전체)")
    parser.add_argument("--out", default=None, help="결과를 쓸 JSON 경로")
    parser.add_argument("--delay", type=float, default=1.0, help="같은 호스트에 대한 요청 간격(초)")
    parser.add_argument("--depth", type=int, default=3, help="메뉴를 따라 들어갈 깊이")
    parser.add_argument("--page-budget", type=int, default=80, help="호스트당 열어볼 페이지 수 상한")
    parser.add_argument("--workers", type=int, default=6, help="동시에 조사할 호스트 수")
    args = parser.parse_args(argv)

    pairs = [(h, "khu_board") for h in args.hosts] if args.hosts else load_hosts()
    fetcher = Fetcher(delay=args.delay)

    def run(pair: tuple[str, str]) -> dict:
        host, adapter = pair
        if adapter == "gnuboard":
            finding = crawl_gnuboard_host(host, fetcher, page_budget=args.page_budget)
        else:
            finding = crawl_host(host, fetcher, depth=args.depth, page_budget=args.page_budget)
        finding.setdefault("adapter", adapter)
        usable = sum(1 for b in finding["boards"] if b["usable"])
        print(
            f"{host}: home={finding['home_status']} pages={finding['pages_fetched']} "
            f"boards={len(finding['boards'])} usable={usable}",
            flush=True,
        )
        return finding

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        findings = list(pool.map(run, pairs))

    payload = {
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hosts_probed": len(pairs),
        "hosts_reachable": sum(1 for f in findings if f["home_status"] == 200),
        "boards_found": sum(len(f["boards"]) for f in findings),
        "boards_usable": sum(1 for f in findings for b in f["boards"] if b["usable"]),
        "crawl": {"depth": args.depth, "page_budget": args.page_budget, "delay": args.delay},
        "findings": sorted(findings, key=lambda f: f["host"]),
    }
    if args.out:
        target = Path(args.out)
        if not target.is_absolute():
            target = REPO_ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"boards found={payload['boards_found']} usable={payload['boards_usable']} -> {target}")
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
