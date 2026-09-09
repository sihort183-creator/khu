"""공개 파일 증분 생성의 "이전 판" 기록(docs/공개파일_증분화_2026-09-09.md).

왜 있나. 2026-09-09 Supabase 가 무료 egress 한도(월 5 GB)를 넘겼다고 알려 왔다.
원인은 매시간 도는 공개 파일 만들기가 보이는 공지 전부를 본문까지 다시 읽는 것이었다.
그래서 회차마다 "무엇이 바뀌었는지"만 가볍게 확인하고, 바뀐 것만 본문까지 읽는다.

바뀐 것을 알아내려면 이전 판이 무엇이었는지 알아야 한다. 그 기록이 이 파일이다.
개정마다 하나씩 R2 에 올린다(``v1/state/<개정>.json``). R2 는 나가는 데이터가 무료라
다음 회차가 이걸 다시 받아 오는 것은 비용이 들지 않는다.

무엇을 담나.

- ``fingerprint`` — 공지 파일 내용에 함께 영향을 주는 "공유 정보"의 지문.
  출처 이름·조직 이름·캠퍼스 이름·공개 하한·중계 주소·코드 판이 여기 들어간다.
  하나라도 달라지면 이전 판을 재사용할 근거가 사라지므로 그 회차는 전체로 돈다.
- ``notices`` — 공개한 공지마다 수정 시각·현재 개정 id 와 **목록 항목 그대로**.
  목록 항목까지 담는 이유는 제목·발췌만 읽어도 회차당 5 MB 가 넘기 때문이다
  (2026-09-09 실측: 공지 23,831건, 제목+발췌 5.1 MB, notices 행 전체 26.6 MB).
- ``excluded`` — 공개 범위 밖이라 내보내지 않은 공지. 이걸 적어 두지 않으면
  매 회차 "처음 보는 공지"로 취급해 본문까지 다시 읽는다.

담지 않는 것: 본문(body_html/body_text). 본문은 상세 파일 객체로 이미 R2 에 있고,
그 객체는 내용 해시로 이름이 붙어 있어 내용이 같으면 경로만 물려주면 된다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

log = logging.getLogger("khu.export.state")

STATE_VERSION = 1
STATE_PREFIX = "v1/state"

# 공지 목록·상세 파일의 모양을 바꾸는 코드 수정이 있으면 이 값을 올린다.
# 올리면 다음 회차가 전체 방식으로 돌아 모든 파일을 다시 만든다.
EXPORT_FORMAT_VERSION = "1"


def state_key(revision: str) -> str:
    return f"{STATE_PREFIX}/{revision}.json"


def iso_stamp(value: datetime | date | None) -> str | None:
    """기록에 넣는 시각 표기. 회차마다 같은 값이 나와야 비교가 성립한다."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value.tzinfo else value.isoformat()
    return value.isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass
class NoticeState:
    """공지 하나의 이전 판. ``entry`` 는 목록 파일에 실렸던 항목 그대로다."""

    updated_at: str | None
    revision_id: str | None
    entry: dict[str, Any] | None = None

    @property
    def adjusted(self) -> bool:
        """보이는 날짜가 "처음 본 시각"으로 바뀐 공지인가.

        원문 날짜가 미래인 공지는 시간이 지나면 미래가 아니게 되어 보이는 날짜가
        저절로 달라진다. 수정 시각은 그대로이므로 이런 공지는 늘 다시 만든다.
        """
        return bool((self.entry or {}).get("published_adjusted"))


@dataclass
class ExportState:
    revision: str
    generated_at: str
    fingerprint: str
    mode: str = "full"
    last_full_at: str | None = None
    notices: dict[str, NoticeState] = field(default_factory=dict)
    excluded: dict[str, NoticeState] = field(default_factory=dict)

    def to_json(self) -> bytes:
        payload = {
            "version": STATE_VERSION,
            "revision": self.revision,
            "generated_at": self.generated_at,
            "fingerprint": self.fingerprint,
            "mode": self.mode,
            "last_full_at": self.last_full_at,
            "notices": {
                key: {"u": row.updated_at, "r": row.revision_id, "e": row.entry}
                for key, row in self.notices.items()
            },
            "excluded": {
                key: {"u": row.updated_at, "r": row.revision_id}
                for key, row in self.excluded.items()
            },
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> ExportState | None:
        try:
            payload = json.loads(data)
        except ValueError:
            return None
        if not isinstance(payload, dict) or payload.get("version") != STATE_VERSION:
            return None
        try:
            return cls(
                revision=str(payload["revision"]),
                generated_at=str(payload.get("generated_at") or ""),
                fingerprint=str(payload.get("fingerprint") or ""),
                mode=str(payload.get("mode") or "full"),
                last_full_at=payload.get("last_full_at"),
                notices={
                    key: NoticeState(row.get("u"), row.get("r"), row.get("e"))
                    for key, row in (payload.get("notices") or {}).items()
                },
                excluded={
                    key: NoticeState(row.get("u"), row.get("r"))
                    for key, row in (payload.get("excluded") or {}).items()
                },
            )
        except (AttributeError, KeyError, TypeError):
            return None


def load_state(store, bucket: str, revision: str) -> ExportState | None:
    """이전 판 기록을 R2 에서 받아 온다. 없거나 깨졌으면 None 이다."""
    try:
        raw = store.get_bytes(bucket, state_key(revision))
    except Exception:  # noqa: BLE001 - 못 받아 오면 그냥 전체 방식으로 돈다
        log.info("이전 판 기록을 받지 못했습니다: %s", state_key(revision))
        return None
    return ExportState.from_json(raw)


def save_state(store, bucket: str, state: ExportState) -> int:
    data = state.to_json()
    store.put_bytes(
        bucket, state_key(state.revision), data,
        content_type="application/json; charset=utf-8", compress=True,
    )
    return len(data)


def latest_revision(store, bucket: str) -> str | None:
    try:
        payload = json.loads(store.get_bytes(bucket, "v1/latest.json"))
    except Exception:  # noqa: BLE001 - 첫 실행이면 포인터가 아직 없다
        return None
    revision = payload.get("revision")
    return str(revision) if revision else None


def load_manifest(store, bucket: str, revision: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(store.get_bytes(bucket, f"v1/r/{revision}/manifest.json"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict) or payload.get("revision") != revision:
        return None
    return payload


def shared_fingerprint(parts: dict[str, Any]) -> str:
    """공유 정보의 지문. 순서를 고정해 같은 입력이면 같은 값이 나오게 한다."""
    blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------- 회차 방식 판정


@dataclass
class ExportPlan:
    """이번 회차를 어떻게 돌지."""

    mode: str  # "full" 또는 "incremental"
    reason: str
    total: int = 0
    changed: set[str] = field(default_factory=set)
    reused: set[str] = field(default_factory=set)
    excluded_kept: set[str] = field(default_factory=set)
    dropped: list[str] = field(default_factory=list)
    added: int = 0

    @property
    def changed_ratio(self) -> float:
        return (len(self.changed) / self.total) if self.total else 1.0

    def summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "total": self.total,
            "changed": len(self.changed),
            "reused": len(self.reused),
            "excluded_kept": len(self.excluded_kept),
            "dropped": len(self.dropped),
            "added": self.added,
        }


def needs_daily_full(
    *, now: datetime, last_full_at: datetime | None, hour_kst: int, every_hours: int, kst
) -> str | None:
    """하루 한 번 전체 재생성을 해야 하는가. 이유를 돌려주고, 아니면 None.

    증분은 "이전 판이 옳다"는 가정 위에 서 있다. 그 가정을 하루에 한 번은
    맨바닥에서 다시 세운다. 판정은 화면 기준 시간대(KST)로 한다.
    """
    if last_full_at is None:
        return "직전 전체 재생성 시각을 모름"
    gap = now - last_full_at
    if gap >= timedelta(hours=max(1, every_hours) + 2):
        return f"마지막 전체 재생성이 {gap.total_seconds() / 3600:.0f}시간 전"
    here = now.astimezone(kst)
    there = last_full_at.astimezone(kst)
    if here.date() > there.date() and here.hour >= hour_kst:
        return f"오늘({here.date().isoformat()}) 첫 전체 재생성"
    return None


def plan_export(
    *,
    requested_mode: str,
    live: list[tuple[str, datetime | None, str | None]],
    state: ExportState | None,
    manifest_entries: dict[str, str] | None,
    existing_objects: set[str] | None,
    fingerprint: str,
    now: datetime,
    kst,
    max_changed_ratio: float,
    full_every_hours: int,
    full_hour_kst: int,
    skip_daily_full: bool = False,
) -> ExportPlan:
    """가벼운 전체 훑기 결과와 이전 판을 견주어 이번 회차 방식을 정한다.

    ``live`` 는 (공지 id, 수정 시각, 현재 개정 id) 목록이다. 본문은 아직 읽지 않았다.
    돌려주는 계획의 ``changed`` 에 든 공지만 본문까지 읽는다.
    """
    total = len(live)
    if requested_mode == "full":
        return ExportPlan(mode="full", reason="전체 방식 요청", total=total)
    if state is None:
        return ExportPlan(mode="full", reason="이전 판 기록이 없음", total=total)
    if not state.notices and not state.excluded:
        return ExportPlan(mode="full", reason="이전 판 기록이 비어 있음", total=total)
    if state.fingerprint != fingerprint:
        return ExportPlan(mode="full", reason="공유 정보(출처·조직·설정·코드)가 바뀜", total=total)
    if manifest_entries is None:
        return ExportPlan(mode="full", reason="이전 개정 명세를 받지 못함", total=total)

    daily = None if skip_daily_full else needs_daily_full(
        now=now, last_full_at=_parse_dt(state.last_full_at),
        hour_kst=full_hour_kst, every_hours=full_every_hours, kst=kst,
    )
    if daily:
        return ExportPlan(mode="full", reason=f"하루 한 번 전체 재생성 — {daily}", total=total)

    plan = ExportPlan(mode="incremental", reason="이전 판과 견주어 바뀐 것만 다시 읽음", total=total)
    for notice_id, updated_at, revision_id in live:
        stamp = iso_stamp(updated_at)
        previous = state.notices.get(notice_id)
        if previous is not None:
            same = (
                previous.updated_at == stamp
                and previous.revision_id == revision_id
                and previous.entry is not None
                and not previous.adjusted
            )
            if same:
                key = f"notices/{notice_id}.json"
                target = manifest_entries.get(key)
                if target and (existing_objects is None or target in existing_objects):
                    plan.reused.add(notice_id)
                    continue
            plan.changed.add(notice_id)
            continue
        skipped = state.excluded.get(notice_id)
        if skipped is not None and skipped.updated_at == stamp and skipped.revision_id == revision_id:
            # 지난번에도 공개 범위 밖이었고 그 뒤로 바뀌지 않았다. 다시 읽을 이유가 없다.
            plan.excluded_kept.add(notice_id)
            continue
        plan.changed.add(notice_id)
        plan.added += 1

    seen = {notice_id for notice_id, _, _ in live}
    plan.dropped = sorted((set(state.notices) | set(state.excluded)) - seen)

    if plan.changed_ratio > max_changed_ratio:
        return ExportPlan(
            mode="full",
            reason=(
                f"바뀐 공지 {len(plan.changed)}건이 전체 {total}건의 "
                f"{plan.changed_ratio:.0%}로 임계({max_changed_ratio:.0%})를 넘음"
            ),
            total=total,
        )
    return plan


__all__ = [
    "EXPORT_FORMAT_VERSION",
    "iso_stamp",
    "ExportPlan",
    "ExportState",
    "NoticeState",
    "STATE_PREFIX",
    "STATE_VERSION",
    "latest_revision",
    "load_manifest",
    "load_state",
    "needs_daily_full",
    "plan_export",
    "save_state",
    "shared_fingerprint",
    "state_key",
]
