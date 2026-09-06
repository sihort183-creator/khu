"""수집 대상 호스트 확정(0단계 조사용).

경희대 CMS 사이트 가이드(`sites.json`)가 정본이지만 빠짐이 있다. 가이드의 도메인 칸이
실제와 다른 곳이 있어서(예: 국제처는 가이드에 `oiak.khu.ac.kr` 로 적혀 있으나 실제
사이트는 `oia.khu.ac.kr` 에 있다), 가이드만 믿으면 국제처·취업·학사처럼 공지가 가장
많이 올라오는 곳을 통째로 놓친다.

그래서 두 갈래를 합친다.
  1. 사이트 가이드 명부(`sites.json`)
  2. 알려진 사이트들이 서로 링크한 `*.khu.ac.kr` 주소를 모아 실제로 열어 본 결과

각 호스트의 게시판 엔진을 확인해 `khu_board` / `gnuboard` 로 나눈다. 게시판이 없는
로그인·예약 시스템은 수집 대상에서 빼되, 판단 근거를 남긴다.

사용:
    python scripts/discover_hosts.py --out registry/bootstrap/hosts.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UA = "khu-notice-bot/0.1 (+https://github.com/sihort183-creator/khu)"
SITES_PATH = REPO_ROOT / "registry" / "bootstrap" / "sites.json"

KHU_HOST = re.compile(r"https?://([a-z0-9][a-z0-9-]*\.khu\.ac\.kr)", re.IGNORECASE)
CMS_BOARD = re.compile(r"/[A-Za-z0-9_]+/user/bbs/[A-Za-z0-9]+/list\.do\?menuNo=\d+")
GNU_BOARD = re.compile(r"/bbs/board\.php\?bo_table=[A-Za-z0-9_]+")
CMS_PAGE = re.compile(r"/[A-Za-z0-9_]+/user/(?:main|contents)/")
TITLE_TAG = re.compile(r"<title>([^<]*)")

# 게시판이 아니라 로그인·예약·메일 같은 시스템이라 수집 대상이 아니다.
KNOWN_SYSTEMS = {
    "mail", "web", "member", "info21", "eip", "sugang", "klas", "e-campus",
    "chat", "aladdin", "apply", "application", "nominate", "professor",
    "foreignplace", "geplace", "studio", "commons", "gafcapply",
}


def fetch(url: str, timeout: float = 15.0) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()[:400_000].decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception:
        return 0, ""


def probe(host: str) -> dict:
    """호스트를 열어 게시판 엔진을 판정한다."""
    status, html = fetch(f"https://{host}/")
    record: dict = {"host": host, "status": status, "adapter": None, "board_hint": 0, "title": None}
    if status != 200 or not html:
        record["engine"] = "unreachable"
        record["note"] = "첫 화면을 열지 못했다."
        return record

    title = TITLE_TAG.search(html)
    record["title"] = re.sub(r"\s+", " ", title.group(1)).strip()[:60] if title else None

    cms = len(set(CMS_BOARD.findall(html)))
    gnu = len(set(GNU_BOARD.findall(html)))
    if cms:
        record.update(engine="khu_cms", adapter="khu_board", board_hint=cms)
    elif gnu:
        record.update(engine="gnuboard", adapter="gnuboard", board_hint=gnu)
    elif CMS_PAGE.search(html):
        # 같은 CMS 지만 첫 화면에 게시판을 걸어 두지 않았다. 메뉴를 훑어야 안다.
        record.update(engine="khu_cms", adapter="khu_board", board_hint=0)
    elif host.split(".")[0] in KNOWN_SYSTEMS:
        record.update(engine="system", note="게시판이 아닌 시스템이다.")
    else:
        record.update(engine="unknown", note="아는 게시판 엔진이 아니다. 어댑터가 필요하다.")
    return record


def harvest(seed_hosts: list[str], workers: int = 12) -> dict[str, int]:
    """알려진 사이트들이 링크한 khu.ac.kr 주소를 모은다."""
    seeds = ["https://www.khu.ac.kr/"] + [f"https://{h}/" for h in seed_hosts]
    seen: dict[str, int] = {}

    def scan(url: str) -> list[str]:
        return KHU_HOST.findall(fetch(url)[1])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for hosts in pool.map(scan, seeds):
            for host in hosts:
                key = host.lower()
                seen[key] = seen.get(key, 0) + 1
    return seen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="경희대 수집 대상 호스트 확정")
    parser.add_argument("--out", default=None, help="결과를 쓸 JSON 경로")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args(argv)

    if not SITES_PATH.exists():
        raise SystemExit(f"{SITES_PATH} 가 없습니다. 먼저 scripts/discover_sites.py 를 실행하세요.")
    guide_hosts = sorted(json.loads(SITES_PATH.read_text(encoding="utf-8"))["hosts"])

    referenced = harvest(guide_hosts, workers=args.workers)
    all_hosts = sorted(set(guide_hosts) | set(referenced))

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        records = list(pool.map(probe, all_hosts))

    for record in records:
        host = record["host"]
        record["in_site_guide"] = host in set(guide_hosts)
        record["referenced_by"] = referenced.get(host, 0)

    collectible = [r for r in records if r["adapter"]]
    by_adapter: dict[str, list[str]] = {}
    for record in collectible:
        by_adapter.setdefault(record["adapter"], []).append(record["host"])

    payload = {
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "revalidate": "학기마다 한 번. 새 사이트가 생기거나 도메인이 바뀔 때만 달라진다.",
        "note": (
            "사이트 가이드 명부와, 사이트끼리 링크한 주소를 합쳐 실제로 열어 본 결과다."
            " 가이드의 도메인 칸이 실제와 다른 곳이 있어(oia/oiak) 가이드만으로는 부족하다."
        ),
        "host_count": len(records),
        "collectible_count": len(collectible),
        "hosts_by_adapter": {k: sorted(v) for k, v in sorted(by_adapter.items())},
        "hosts": sorted(records, key=lambda r: r["host"]),
    }

    if args.out and args.out != "-":
        target = Path(args.out)
        if not target.is_absolute():
            target = REPO_ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(
            f"호스트 {len(records)}개 중 수집 대상 {len(collectible)}개 "
            f"({ {k: len(v) for k, v in by_adapter.items()} }) -> {target}"
        )
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
