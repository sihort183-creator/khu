"""경희대 공통 게시판 탐색 도구(0단계 조사용).

주어진 도메인의 첫 화면에서 `/user/bbs/{board}/list.do?menuNo={n}` 형태의 게시판을 찾고,
각 게시판이 실제로 목록을 돌려주는지 확인한 결과를 JSON 으로 남긴다.

사용:
    python scripts/discover_sources.py --out registry/bootstrap/discovered.json
    python scripts/discover_sources.py --hosts cs.khu.ac.kr swcon.khu.ac.kr

이 도구는 조사 결과를 만들 뿐이고 운영 데이터베이스에 아무것도 쓰지 않는다.
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
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
UA = "khu-notice-bot/0.1 (+https://github.com/sihort183-creator/khu)"

# 조사 후보. 경희대 공식 사이트에서 확인된 도메인 형태를 따른다.
DEFAULT_HOSTS = [
    "www.khu.ac.kr",
    "cs.khu.ac.kr",
    "ce.khu.ac.kr",
    "swcon.khu.ac.kr",
    "ee.khu.ac.kr",
    "software.khu.ac.kr",
    "law.khu.ac.kr",
    "khsma.khu.ac.kr",
    "kbiz.khu.ac.kr",
    "science.khu.ac.kr",
    "foreign.khu.ac.kr",
    "hc.khu.ac.kr",
    "eng.khu.ac.kr",
    "medicine.khu.ac.kr",
    "nursing.khu.ac.kr",
    "pharm.khu.ac.kr",
    "dent.khu.ac.kr",
    "korme.khu.ac.kr",
    "art.khu.ac.kr",
    "music.khu.ac.kr",
    "dance.khu.ac.kr",
    "sports.khu.ac.kr",
    "hm.khu.ac.kr",
    "life.khu.ac.kr",
    "applied.khu.ac.kr",
    "elec.khu.ac.kr",
    "me.khu.ac.kr",
    "civil.khu.ac.kr",
    "chemeng.khu.ac.kr",
    "ibs.khu.ac.kr",
    "space.khu.ac.kr",
    "physics.khu.ac.kr",
    "chem.khu.ac.kr",
    "math.khu.ac.kr",
    "bio.khu.ac.kr",
    "geo.khu.ac.kr",
    "politics.khu.ac.kr",
    "public.khu.ac.kr",
    "media.khu.ac.kr",
    "econ.khu.ac.kr",
    "trade.khu.ac.kr",
    "hotel.khu.ac.kr",
    "tourism.khu.ac.kr",
    "swedu.khu.ac.kr",
    "startup.khu.ac.kr",
    "oia.khu.ac.kr",
    "neoscholar.khu.ac.kr",
    "intern.khu.ac.kr",
    "lib.khu.ac.kr",
]

LIST_LINK = re.compile(r'href="([^"]*?/user/bbs/([A-Za-z0-9]+)/list\.do\?menuNo=(\d+))"')
TITLE_TAG = re.compile(r"<title>([^<]*)</title>")
BOARD_TABLE = re.compile(r'<table[^>]*class="[^"]*board01')
VIEW_CALL = re.compile(r"view\('([^']+)'")


def fetch(url: str, timeout: float = 20.0) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return response.status, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception:
        return 0, ""


def nav_label(html: str, href: str) -> str | None:
    """게시판 링크의 표시 이름을 찾는다."""
    index = html.find(f'href="{href}"')
    if index < 0:
        return None
    tail = html[index : index + 400]
    match = re.search(r">\s*([^<>]{2,40}?)\s*<", tail)
    if not match:
        return None
    label = re.sub(r"\s+", " ", match.group(1)).strip()
    return label or None


def probe_host(host: str, *, delay: float = 1.5, max_boards: int = 20) -> dict:
    base = f"https://{host}"
    status, home = fetch(base + "/")
    result: dict = {"host": host, "home_status": status, "boards": [], "error": None}
    if status != 200 or not home:
        result["error"] = "첫 화면을 열지 못했습니다."
        return result

    site_title = TITLE_TAG.search(home)
    result["title"] = re.sub(r"\s+", " ", site_title.group(1)).strip() if site_title else None

    seen: set[tuple[str, str]] = set()
    for href, board_code, menu_no in LIST_LINK.findall(home):
        key = (board_code, menu_no)
        if key in seen:
            continue
        seen.add(key)
        if len(seen) > max_boards:
            break

        url = href if href.startswith("http") else base + href
        prefix = urlsplit(url).path.strip("/").split("/")[0]
        time.sleep(delay)
        board_status, board_html = fetch(url)
        rows = len(VIEW_CALL.findall(board_html)) if board_html else 0
        result["boards"].append(
            {
                "board_code": board_code,
                "menu_no": menu_no,
                "prefix": prefix,
                "url": url,
                "label": nav_label(home, href),
                "status": board_status,
                "has_table": bool(board_html and BOARD_TABLE.search(board_html)),
                "row_count": rows,
                "usable": bool(board_status == 200 and rows > 0),
            }
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="경희대 게시판 출처 조사")
    parser.add_argument("--hosts", nargs="*", default=None, help="조사할 도메인 목록")
    parser.add_argument("--out", default=None, help="결과를 쓸 JSON 경로")
    parser.add_argument("--delay", type=float, default=1.5, help="요청 간격(초)")
    args = parser.parse_args(argv)

    hosts = args.hosts or DEFAULT_HOSTS
    findings = []
    for host in hosts:
        finding = probe_host(host, delay=args.delay)
        usable = sum(1 for b in finding["boards"] if b["usable"])
        print(f"{host}: home={finding['home_status']} boards={len(finding['boards'])} usable={usable}", flush=True)
        findings.append(finding)
        time.sleep(args.delay)

    payload = {
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hosts_probed": len(hosts),
        "hosts_reachable": sum(1 for f in findings if f["home_status"] == 200),
        "boards_usable": sum(1 for f in findings for b in f["boards"] if b["usable"]),
        "findings": findings,
    }
    if args.out:
        target = Path(args.out)
        if not target.is_absolute():
            target = REPO_ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {target}")
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
