"""하한보다 오래된 고정 공지 가운데 사람이 골라 공개하는 것(``registry/pinned_exceptions.json``).

왜 있나. 게시판 맨 위에 고정된 글은 등록일이 오래돼도 지금 유효한 규정인 경우가 있어
공개 범위 밖이라도 보여준다(app.domain.dates 참고). 다만 게시판이 옛 소식을 고정해 둔
것까지 끌려오면 안 되므로 ``PINNED_WINDOW_FLOOR`` 로 하한을 뒀고, 그 아래 83건에는
"소프트웨어융합학과 다전공 필독 사항"(2018) 같은 현행 규정과 "2020 QS 세계대학평가"
같은 지난 소식이 섞여 있다. 기계로는 가릴 수 없어 사용자 결정(2026-09-14)에 따라
사람이 읽고 고른 64건만 개별 예외로 공개한다.

**왜 목록을 코드가 아니라 데이터로 두나.** 나중에 사람이 더하고 빼야 하는 목록이기
때문이다. 코드에 박으면 목록을 고치는 데 배포가 필요하고, 무엇이 왜 들어 있는지도
알아보기 어렵다. 파일에는 제목·조직·발행일·근거를 함께 적어 두어 나중에 보는 사람이
식별자만 보고 판단하는 일이 없게 했다.

**왜 공지 식별자가 아니라 원문 주소로 짚나.** ``ntc-…`` 는 대표 원본에서
``blake2b(adapter, list_url, external_id)`` 로 만드는 결정론적 값이라(app.domain.ids)
재수집·재분류로는 바뀌지 않는다 — 2026-09-14 실측으로 64건 전부가 자연키에서 저장값
그대로 다시 계산됐다. 그러나 두 가지에 흔들린다. 등록부의 ``list_url`` 이 바뀌면
출처 식별자가 바뀌어 공지 식별자까지 따라 바뀌고, 중복 병합으로 이 글이 다른 공지에
흡수되면 그 공지는 숨김이 된다(같은 날 운영 DB 에 병합 기록이 1,290건 있었다).
원문 주소는 앞의 것에 흔들리지 않고 사람이 눌러 확인할 수도 있다. 뒤의 것(병합)은
어느 열쇠로도 못 막으므로 대신 못 찾은 줄을 회차마다 경고로 남긴다(``unmatched``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import REPO_ROOT

log = logging.getLogger("khu.domain.pinned")

EXCEPTIONS_PATH = REPO_ROOT / "registry" / "pinned_exceptions.json"


@dataclass(frozen=True)
class PinnedException:
    """예외 목록의 한 줄. ``url`` 만 판정에 쓰고 나머지는 사람이 읽을 몫이다."""

    url: str
    title: str
    organization: str
    published_date: str
    notice_id: str

    @property
    def label(self) -> str:
        """경고 한 줄에 쓸 표기. 제목만으로는 어느 학과 것인지 알 수 없다."""
        return f"{self.title} ({self.organization}, {self.published_date})"


@lru_cache(maxsize=4)
def load_exceptions(path: Path | None = None) -> tuple[PinnedException, ...]:
    """예외 목록을 읽는다. 파일이 없으면 빈 목록이다(예외를 하나도 두지 않은 것과 같다).

    파일이 깨져 있으면 조용히 넘기지 않고 올린다. 예외가 통째로 사라지면 64건이 말없이
    안 보이게 되는데, 그 편이 회차가 실패하는 것보다 알아채기 어렵다.
    """
    target = path or EXCEPTIONS_PATH
    if not target.exists():
        log.warning("고정 공지 예외 목록이 없습니다: %s", target)
        return ()
    payload = json.loads(target.read_text(encoding="utf-8"))
    rows = payload.get("notices") or []
    out: list[PinnedException] = []
    seen: set[str] = set()
    for row in rows:
        url = str(row.get("url") or "").strip()
        if not url:
            raise ValueError(f"고정 공지 예외 목록에 url 이 없는 줄이 있습니다: {row!r}")
        if url in seen:
            raise ValueError(f"고정 공지 예외 목록에 같은 url 이 두 번 있습니다: {url}")
        seen.add(url)
        out.append(
            PinnedException(
                url=url,
                title=str(row.get("title") or ""),
                organization=str(row.get("organization") or ""),
                published_date=str(row.get("published_date") or ""),
                notice_id=str(row.get("notice_id") or ""),
            )
        )
    return tuple(out)


def exception_urls(path: Path | None = None) -> frozenset[str]:
    """판정에 쓰는 주소 집합. 부르는 쪽이 회차마다 한 번만 읽어 아래로 넘긴다."""
    return frozenset(row.url for row in load_exceptions(path))


def unmatched(found: set[str], path: Path | None = None) -> tuple[PinnedException, ...]:
    """목록에 있는데 실제로 공개 대상에서 찾지 못한 줄.

    ``found`` 는 이번에 실제로 짚인 주소다. 돌려주는 줄들은 식별자가 어긋났거나, 글이
    지워졌거나, 중복 병합으로 다른 공지에 흡수된 것이다. 조용히 넘기면 아무도 모른다.
    """
    return tuple(row for row in load_exceptions(path) if row.url not in found)


def warn_unmatched(found: set[str], path: Path | None = None) -> tuple[PinnedException, ...]:
    """``unmatched`` 를 구해 경고로 남긴다. 돌려주는 값은 부르는 쪽이 세어 쓰라고 준다."""
    missing = unmatched(found, path)
    if missing:
        log.warning(
            "고정 공지 예외 %d건을 공개 대상에서 찾지 못했습니다(지워졌거나 병합됐을 수 있습니다): %s",
            len(missing),
            " / ".join(f"{row.label} {row.url}" for row in missing),
        )
    return missing


__all__ = [
    "EXCEPTIONS_PATH",
    "PinnedException",
    "exception_urls",
    "load_exceptions",
    "unmatched",
    "warn_unmatched",
]
