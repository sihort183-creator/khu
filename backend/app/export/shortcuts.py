"""바로가기 카드 자료를 저장소 파일에서 읽는다(``data/shortcuts/``).

**DB 를 타지 않는다.** 지난 두 번의 무료 한도 사고가 전부 "DB 를 읽고 쓰는 경로"에서
났다(설계문서 6절). 연락처처럼 표에 적재하면 회차마다 읽고 내보내는 대상이 하나 더
늘어난다. 카드는 사람이 손으로 고치는 정적 자료라 그럴 이유가 없다 — 저장소 파일을
읽어 그대로 R2 로 보낸다.

**회차마다 파일을 다시 읽지 않는다.** ``lru_cache`` 로 프로세스마다 한 번만 읽는다.
내용이 같으면 ``Writer`` 가 내용 해시로 같은 객체를 가리키므로 업로드도 일어나지 않는다.

두 파일이 가는 곳이 다르다.

``cards.json``
    카드 13장. ``catalog.json`` 의 ``data.shortcut_cards`` 로 **얹는다**. 화면이 어차피
    받는 파일이라 학생당 추가 요청이 0건이다(gzip 751B → 13KB).
``graduation_requirements.json``
    학과 88곳의 졸업요건. gzip 41KB 라 얹지 않고 **따로 둔다**. 졸업요건 카드가 실제로
    떴을 때만 받으므로 공지만 보는 학생은 평생 받지 않는다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import REPO_ROOT

SHORTCUTS_DIR = REPO_ROOT / "data" / "shortcuts"
CARDS_PATH = SHORTCUTS_DIR / "cards.json"
GRADUATION_PATH = SHORTCUTS_DIR / "graduation_requirements.json"

#: 개정 경로 안에서 학과별 졸업요건이 놓이는 자리. 화면(lib/api.ts)이 같은 이름으로 받는다.
GRADUATION_KEY = "shortcuts/graduation.json"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_cards(path: Path | None = None) -> tuple[dict[str, Any], ...]:
    """카드 13장. 파일이 없으면 빈 목록이다(카드를 한 장도 안 띄운 것과 같다).

    파일의 ``meta`` 는 어디서 왔는지 적어 둔 작업 기록이라 화면으로 내보내지 않는다.
    깨져 있으면 조용히 넘기지 않고 올린다 — 카드가 말없이 사라지는 편이 알아채기 어렵다.
    """
    target = path or CARDS_PATH
    if not target.exists():
        return ()
    payload = _read(target)
    cards = payload.get("cards") if isinstance(payload, dict) else payload
    if not isinstance(cards, list):
        raise ValueError(f"바로가기 카드 파일에 cards 배열이 없습니다: {target}")
    ids = [card.get("id") for card in cards]
    if len(set(ids)) != len(ids):
        raise ValueError("바로가기 카드에 같은 id 가 두 번 있습니다")
    return tuple(cards)


@lru_cache(maxsize=1)
def load_graduation(path: Path | None = None) -> dict[str, Any] | None:
    """학과별 졸업요건. 파일이 없으면 ``None`` 이고 그 파일을 내보내지 않는다."""
    target = path or GRADUATION_PATH
    if not target.exists():
        return None
    payload = _read(target)
    if not isinstance(payload, dict) or not isinstance(payload.get("departments"), list):
        raise ValueError(f"졸업요건 파일에 departments 배열이 없습니다: {target}")
    return payload
