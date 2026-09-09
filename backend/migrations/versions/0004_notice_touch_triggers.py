"""공지 수정 시각을 데이터베이스가 스스로 올리게 한다.

왜 필요한가. 2026-09-09부터 공개 파일을 증분으로 만든다. 무엇이 바뀌었는지는
``notices.updated_at`` 하나로 판정한다. 그런데 지금은 앱 코드가 그 값을 손으로
올린다. 어느 한 갈래에서 잊으면 바뀐 공지가 "안 바뀐 것"으로 분류되어 낡은 파일이
그대로 공개된다. 조용히 틀리는 종류의 고장이라 눈에 띄지도 않는다.

그래서 공지 내용에 실제로 영향을 주는 표가 바뀌면 데이터베이스가 관련 공지의
수정 시각을 스스로 올린다. 대상은 여섯 곳이다.

    notice_categories          분류
    notice_audiences           대상
    notice_sources             출처 연결(상세의 sources[] 와 source_count)
    attachments                첨부 (개정에 딸린다)
    notice_contact_mentions    본문 문의처 (개정에 딸린다)
    source_items               원본(개정 교체·원문 주소·상태·고정 여부)
    source_item_revisions      개정 본문을 제자리에서 고친 경우

무엇을 일부러 뺐나. ``source_items.last_seen_at`` 같은 값은 회차마다 거의 모든
행이 바뀐다. 그것까지 세면 매 회차 전부가 "바뀐 공지"가 되어 증분이 무의미해진다.
그래서 source_items 는 행 단위 트리거에 WHEN 조건을 달아, 공개 파일에 실제로
실리는 네 열이 달라진 경우에만 몸통을 실행한다.

자식 표 셋은 문단위(FOR EACH STATEMENT) 트리거와 전이 표를 쓴다. 분류를 통째로
다시 넣는 것 같은 대량 작업에서 행마다 트리거가 도는 것을 피한다.

이 마이그레이션은 PostgreSQL 에서만 실제 동작한다. 검사용 SQLite 에는 전이 표도
plpgsql 도 없으므로 스키마 표시만 올리고 지나간다.

되돌리기(downgrade)는 트리거와 함수를 지운다. 표와 자료는 건드리지 않는다.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.db import SCHEMA_STATE_KEY

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


# 자식 표(공지 id 를 직접 들고 있는 표) --------------------------------------

FUNCTIONS = {
    "khu_touch_notices_child_ins": """
create or replace function khu_touch_notices_child_ins() returns trigger
language plpgsql as $khu$
begin
  update notices set updated_at = now()
   where id in (select distinct notice_id from khu_new);
  return null;
end;
$khu$;
""",
    "khu_touch_notices_child_del": """
create or replace function khu_touch_notices_child_del() returns trigger
language plpgsql as $khu$
begin
  update notices set updated_at = now()
   where id in (select distinct notice_id from khu_old);
  return null;
end;
$khu$;
""",
    "khu_touch_notices_child_upd": """
create or replace function khu_touch_notices_child_upd() returns trigger
language plpgsql as $khu$
begin
  update notices set updated_at = now()
   where id in (
     select notice_id from khu_new
     union
     select notice_id from khu_old
   );
  return null;
end;
$khu$;
""",
    # 개정 id 를 들고 있는 표(첨부·문의처). 개정 -> 원본 -> 공지 로 거슬러 올라간다.
    "khu_touch_notices_rev_ins": """
create or replace function khu_touch_notices_rev_ins() returns trigger
language plpgsql as $khu$
begin
  update notices set updated_at = now()
   where id in (
     select n.id from notices n
       join source_items si on si.id = n.primary_source_item_id
      where si.current_revision_id in (select distinct revision_id from khu_new)
     union
     select ns.notice_id from notice_sources ns
       join source_items si2 on si2.id = ns.source_item_id
      where ns.is_active
        and si2.current_revision_id in (select distinct revision_id from khu_new)
   );
  return null;
end;
$khu$;
""",
    "khu_touch_notices_rev_del": """
create or replace function khu_touch_notices_rev_del() returns trigger
language plpgsql as $khu$
begin
  update notices set updated_at = now()
   where id in (
     select n.id from notices n
       join source_items si on si.id = n.primary_source_item_id
      where si.current_revision_id in (select distinct revision_id from khu_old)
     union
     select ns.notice_id from notice_sources ns
       join source_items si2 on si2.id = ns.source_item_id
      where ns.is_active
        and si2.current_revision_id in (select distinct revision_id from khu_old)
   );
  return null;
end;
$khu$;
""",
    "khu_touch_notices_rev_upd": """
create or replace function khu_touch_notices_rev_upd() returns trigger
language plpgsql as $khu$
begin
  update notices set updated_at = now()
   where id in (
     select n.id from notices n
       join source_items si on si.id = n.primary_source_item_id
      where si.current_revision_id in (
              select revision_id from khu_new union select revision_id from khu_old)
     union
     select ns.notice_id from notice_sources ns
       join source_items si2 on si2.id = ns.source_item_id
      where ns.is_active
        and si2.current_revision_id in (
              select revision_id from khu_new union select revision_id from khu_old)
   );
  return null;
end;
$khu$;
""",
    # 원본 한 행. 공개 파일에 실리는 열이 달라졌을 때만 이 몸통이 돈다(WHEN 조건).
    "khu_touch_notices_item_row": """
create or replace function khu_touch_notices_item_row() returns trigger
language plpgsql as $khu$
begin
  update notices set updated_at = now()
   where primary_source_item_id = new.id
      or id in (
           select notice_id from notice_sources
            where source_item_id = new.id and is_active
         );
  return null;
end;
$khu$;
""",
    # 개정 본문을 제자리에서 고친 경우. 보통은 새 개정 행이 생기지만, 고쳐 쓰는
    # 갈래가 생겨도 놓치지 않도록 막아 둔다.
    "khu_touch_notices_revision_row": """
create or replace function khu_touch_notices_revision_row() returns trigger
language plpgsql as $khu$
begin
  update notices set updated_at = now()
   where id in (
     select n.id from notices n
       join source_items si on si.id = n.primary_source_item_id
      where si.current_revision_id = new.id
     union
     select ns.notice_id from notice_sources ns
       join source_items si2 on si2.id = ns.source_item_id
      where ns.is_active and si2.current_revision_id = new.id
   );
  return null;
end;
$khu$;
""",
}

_CHILD_TABLES = ("notice_categories", "notice_audiences", "notice_sources")
_REVISION_TABLES = ("attachments", "notice_contact_mentions")

# 원본에서 공개 파일에 실리는 열. 이 넷이 달라졌을 때만 수정 시각을 올린다.
_ITEM_COLUMNS = ("current_revision_id", "canonical_url", "original_status", "is_pinned")
# 개정에서 공개 파일에 실리는 열.
_REVISION_COLUMNS = ("body_html", "body_text", "title", "published_date")


def _changed(columns: tuple[str, ...]) -> str:
    return "\n        or ".join(f"old.{name} is distinct from new.{name}" for name in columns)


def _triggers() -> dict[str, str]:
    """트리거 이름 -> 만드는 문장."""
    out: dict[str, str] = {}
    for table in _CHILD_TABLES:
        out[f"trg_{table}_touch_ins"] = (
            f"create trigger trg_{table}_touch_ins after insert on {table}"
            " referencing new table as khu_new for each statement"
            " execute function khu_touch_notices_child_ins();"
        )
        out[f"trg_{table}_touch_del"] = (
            f"create trigger trg_{table}_touch_del after delete on {table}"
            " referencing old table as khu_old for each statement"
            " execute function khu_touch_notices_child_del();"
        )
        out[f"trg_{table}_touch_upd"] = (
            f"create trigger trg_{table}_touch_upd after update on {table}"
            " referencing new table as khu_new old table as khu_old for each statement"
            " execute function khu_touch_notices_child_upd();"
        )
    for table in _REVISION_TABLES:
        out[f"trg_{table}_touch_ins"] = (
            f"create trigger trg_{table}_touch_ins after insert on {table}"
            " referencing new table as khu_new for each statement"
            " execute function khu_touch_notices_rev_ins();"
        )
        out[f"trg_{table}_touch_del"] = (
            f"create trigger trg_{table}_touch_del after delete on {table}"
            " referencing old table as khu_old for each statement"
            " execute function khu_touch_notices_rev_del();"
        )
        out[f"trg_{table}_touch_upd"] = (
            f"create trigger trg_{table}_touch_upd after update on {table}"
            " referencing new table as khu_new old table as khu_old for each statement"
            " execute function khu_touch_notices_rev_upd();"
        )
    out["trg_source_items_touch_upd"] = (
        "create trigger trg_source_items_touch_upd after update on source_items"
        f" for each row when ({_changed(_ITEM_COLUMNS)})"
        " execute function khu_touch_notices_item_row();"
    )
    out["trg_source_item_revisions_touch_upd"] = (
        "create trigger trg_source_item_revisions_touch_upd after update on source_item_revisions"
        f" for each row when ({_changed(_REVISION_COLUMNS)})"
        " execute function khu_touch_notices_revision_row();"
    )
    return out


TRIGGERS = _triggers()

TRIGGER_TABLES = {
    **{f"trg_{table}_touch_{op}": table for table in _CHILD_TABLES for op in ("ins", "del", "upd")},
    **{f"trg_{table}_touch_{op}": table for table in _REVISION_TABLES for op in ("ins", "del", "upd")},
    "trg_source_items_touch_upd": "source_items",
    "trg_source_item_revisions_touch_upd": "source_item_revisions",
}


def _is_postgres(bind) -> bool:
    return bind.dialect.name == "postgresql"


def _set_version(value: str) -> None:
    state = sa.table("schema_state", sa.column("key", sa.String), sa.column("value", sa.String))
    op.execute(state.update().where(state.c.key == SCHEMA_STATE_KEY).values(value=value))


def upgrade() -> None:
    bind = op.get_bind()
    if _is_postgres(bind):
        for body in FUNCTIONS.values():
            op.execute(body)
        for name, statement in TRIGGERS.items():
            op.execute(f"drop trigger if exists {name} on {TRIGGER_TABLES[name]};")
            op.execute(statement)
    _set_version("0004")


def downgrade() -> None:
    bind = op.get_bind()
    if _is_postgres(bind):
        for name, table in TRIGGER_TABLES.items():
            op.execute(f"drop trigger if exists {name} on {table};")
        for name in FUNCTIONS:
            op.execute(f"drop function if exists {name}();")
    _set_version("0003")
