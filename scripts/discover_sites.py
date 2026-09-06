"""경희대 공식 사이트 명부 수집(0단계 조사용).

경희대 CMS 는 `사이트 가이드`(neoscholar.khu.ac.kr/guide) 에서 학교가 직접 관리하는
전체 사이트 목록을 JSON 으로 돌려준다. 도메인을 손으로 추측하는 대신 이 명부를
출발점으로 삼는다.

사용:
    python scripts/discover_sites.py --out registry/bootstrap/sites.json

응답에는 관리자 이메일과 외부 서비스 키가 섞여 있다. 저장소에는 공지 수집에
필요한 항목만 남기고 나머지는 버린다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UA = "khu-notice-bot/0.1 (+https://github.com/sihort183-creator/khu)"
GUIDE_ENDPOINT = "https://neoscholar.khu.ac.kr/guide/user/guide/doShowSiteList.json"

# 명부가 돌려주는 항목 중 저장할 것. 담당자 이메일(managerId)과
# kakaoAppKey / googleAnalyticsId 같은 외부 키는 의도적으로 제외한다.
KEEP_FIELDS = (
    "siteSn",
    "siteId",
    "siteNm",
    "siteDc",
    "siteCd",
    "siteSeCd",
    "siteTmpltCd",
    "siteDomain",
    "useAt",
    "siteMaintenance",
    "parentDeptCd",
    "deptCd",
)

# siteCd 는 사이트의 소속 갈래다.
SITE_KIND = {
    "COLLEGE": "college",
    "GRADUATE": "graduate",
    "ATTACHED": "attached",
    "ETC": "etc",
}


def fetch_site_list(timeout: float = 30.0) -> list[dict]:
    request = urllib.request.Request(
        GUIDE_ENDPOINT,
        data=b"",
        headers={
            "User-Agent": UA,
            "Accept-Language": "ko",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if payload.get("resultCode") != "success":
        raise RuntimeError(f"사이트 명부 응답이 성공이 아닙니다: {payload.get('resultCode')}")
    return payload.get("resultList", [])


def normalize_host(site_domain: str | None) -> str | None:
    """siteDomain 값에서 호스트만 뽑는다. 비어 있거나 깨진 값은 버린다."""
    if not site_domain:
        return None
    value = site_domain.strip().rstrip("/")
    # 명부에는 "https:" 처럼 도메인이 빠진 채 저장된 항목도 있다.
    if "//" not in value:
        return None
    host = value.split("//", 1)[1].split("/", 1)[0].strip().lower()
    return host if host.endswith("khu.ac.kr") else None


def trim(record: dict) -> dict:
    kept = {field: record.get(field) for field in KEEP_FIELDS}
    kept["host"] = normalize_host(record.get("siteDomain"))
    kept["kind"] = SITE_KIND.get(record.get("siteCd"), "etc")
    return kept


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="경희대 공식 사이트 명부 수집")
    parser.add_argument("--out", default=None, help="결과를 쓸 JSON 경로")
    args = parser.parse_args(argv)

    records = [trim(record) for record in fetch_site_list()]
    hosts = sorted({record["host"] for record in records if record["host"]})
    without_host = [record["siteId"] for record in records if not record["host"]]

    payload = {
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": GUIDE_ENDPOINT,
        "note": (
            "경희대 CMS 사이트 가이드가 돌려준 공식 명부다. 담당자 이메일과 외부 서비스 키는"
            " 저장하지 않는다. host 가 없는 항목은 명부에 도메인이 비어 있는 사이트다."
        ),
        "site_count": len(records),
        "host_count": len(hosts),
        "sites_without_host": without_host,
        "hosts": hosts,
        "sites": sorted(records, key=lambda r: (r["host"] or "~", r["siteId"])),
    }

    if args.out:
        target = Path(args.out)
        if not target.is_absolute():
            target = REPO_ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"sites={len(records)} hosts={len(hosts)} -> {target}")
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
