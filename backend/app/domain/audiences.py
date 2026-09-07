"""공지 대상 범위(8.2절).

출처의 명시적 기본 대상에서 시작하고, 게시물의 명시적인 제한만으로 좁힌다.
'행정학과 소속'과 '행정학과만 신청 가능'을 같은 뜻으로 취급하지 않는다.
확신이 없으면 대상 미확정으로 두고 전체 공지에서 찾을 수 있게 한다.
출처가 본부라는 이유만으로 전교생 대상이라고 단정하지 않는다(4절 4항).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

RULE_VERSION = "audiences/1"

# 캠퍼스 표기. 등록된 캠퍼스 코드로 옮긴다.
CAMPUS_HINTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("seoul", re.compile(r"서울\s*캠퍼스|서울캠|\(서울\)")),
    ("global", re.compile(r"국제\s*캠퍼스|국제캠|글로벌\s*캠퍼스|\(국제\)")),
)

# 대상을 좁히는 명시적 표현. 소속 언급과 구분한다.
RESTRICTION = re.compile(
    r"(?:에\s*한(?:함|하여|해)|만\s*(?:신청|지원|참여|응모|해당)|대상자?\s*[:：]|"
    r"신청\s*자격|지원\s*자격|참가\s*자격|한정|국한)"
)

# 전교생 대상임을 스스로 밝힌 경우에만 대학 전체로 본다.
UNIVERSITY_WIDE = re.compile(r"전교생|전체\s*학생|재학생\s*전원|모든\s*재학생|전\s*구성원")


@dataclass(frozen=True)
class AudienceTarget:
    type: str  # university | campus | organization | undetermined
    id: str | None
    name: str

    def key(self) -> str:
        if self.type == "university":
            return "university"
        if self.type == "undetermined":
            return "undetermined"
        prefix = "campus" if self.type == "campus" else "org"
        return f"{prefix}:{self.id}"


@dataclass(frozen=True)
class AudienceDecision:
    targets: tuple[AudienceTarget, ...]
    note: str | None
    rule_version: str = RULE_VERSION
    narrowed: bool = False


UNDETERMINED = AudienceTarget(type="undetermined", id=None, name="대상 미확정")


def decide(
    title: str,
    body_text: str | None,
    *,
    source_defaults: tuple[AudienceTarget, ...],
    campus_lookup: dict[str, tuple[str, str]] | None = None,
) -> AudienceDecision:
    """출처 기본 대상 + 게시물의 명시적 제한으로 대상 범위를 정한다.

    campus_lookup: {'seoul': (campus_id, '서울캠퍼스'), ...}
    """
    text = f"{title or ''}\n{(body_text or '')[:3000]}"

    explicit_campus = _explicit_campus(text, campus_lookup or {})
    says_university = bool(UNIVERSITY_WIDE.search(text))
    has_restriction = bool(RESTRICTION.search(text))

    note: str | None = None
    if has_restriction:
        note = "원문에 신청 자격·대상 제한 표현이 있습니다. 실제 자격은 원문에서 확인하세요."

    if says_university and not explicit_campus:
        return AudienceDecision(
            targets=(AudienceTarget(type="university", id=None, name="대학 전체"),),
            note=note,
            narrowed=False,
        )

    if explicit_campus:
        # 게시물이 캠퍼스를 명시하면 그 캠퍼스를 대상에 더한다. 다만 출처 조직을 지우지는
        # 않는다. 지우면 영어영문학과 게시판 글이 본문에 "국제캠퍼스"를 언급했다는 이유로
        # 학과와의 연결을 잃고 "국제캠퍼스 전체" 공지가 되어, 학과를 고른 사람에게는
        # 사라지고 엉뚱한 사람에게는 뜬다. 2026-09-07 실제로 그렇게 보였다.
        # 다만 기본 대상이 대학 전체나 캠퍼스처럼 넓을 때는 게시물이 밝힌 캠퍼스로
        # 좁히는 것이 맞다. 지키는 것은 조직 대상뿐이다.
        keep = tuple(target for target in source_defaults if target.type == "organization")
        return AudienceDecision(targets=keep + tuple(explicit_campus), note=note, narrowed=True)

    if source_defaults:
        return AudienceDecision(targets=tuple(source_defaults), note=note, narrowed=False)

    return AudienceDecision(targets=(UNDETERMINED,), note=note, narrowed=False)


def _explicit_campus(
    text: str, campus_lookup: dict[str, tuple[str, str]]
) -> list[AudienceTarget]:
    found: list[AudienceTarget] = []
    for code, pattern in CAMPUS_HINTS:
        if not pattern.search(text):
            continue
        mapped = campus_lookup.get(code)
        if mapped is None:
            continue
        campus_id, name = mapped
        found.append(AudienceTarget(type="campus", id=campus_id, name=name))
    # 두 캠퍼스가 모두 나오면 좁히는 근거가 아니다.
    if len(found) > 1:
        return []
    return found
