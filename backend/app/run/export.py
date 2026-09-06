"""직렬 공개와 동일 데이터 재사용 실측용 실행 명령."""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict

from app.export.static import export_static


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="공개 파일 생성 및 재사용 실측")
    parser.add_argument("--repeat", type=int, choices=(1, 2), default=1)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for index in range(args.repeat):
        started = time.monotonic()
        # 같은 초에 두 번 완료해도 서로 다른 불변 개정 경로를 사용한다.
        revision = f"r{time.time_ns()}-export{index + 1}"
        result = export_static(revision=revision)
        elapsed = time.monotonic() - started
        print(json.dumps({"attempt": index + 1, "export_seconds": round(elapsed, 3),
                          **asdict(result)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
