"""실행 설정. 값은 환경 변수에서만 읽는다(15.2절 비밀값 규칙)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]

SEOUL_TZ = "Asia/Seoul"
CONTRACT_VERSION = "v1"


def _load_dotenv(path: Path) -> None:
    """.env 가 있으면 읽는다. 이미 설정된 환경 변수는 덮어쓰지 않는다."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(BACKEND_ROOT / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    """필수 설정이 없을 때. 실행을 조용히 계속하지 않는다."""


@dataclass(frozen=True)
class R2Settings:
    account_id: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    bucket_public: str = "khu-notice-public"
    bucket_evidence: str = "khu-notice-evidence"
    bucket_backup: str = "khu-notice-backup"

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    @property
    def configured(self) -> bool:
        return bool(self.account_id and self.access_key_id and self.secret_access_key)


@dataclass(frozen=True)
class Settings:
    env: str = "development"
    database_url: str = ""
    r2: R2Settings = field(default_factory=R2Settings)
    heartbeat_url: str = ""

    run_budget_seconds: int = 1800
    http_concurrency: int = 5
    http_delay_seconds: float = 2.0
    http_timeout_seconds: float = 25.0
    max_response_bytes: int = 8_000_000
    user_agent: str = "khu-notice-bot/0.1 (+https://github.com/sihort183-creator/khu)"
    dry_run: bool = False

    # 수집 범위
    initial_history_days: int = 90
    list_page_limit: int = 5
    recheck_days: int = 14

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def require_database(self) -> str:
        if not self.database_url:
            raise ConfigError(
                "KHU_DATABASE_URL 이 없습니다. backend/.env 또는 Actions 비밀값에 "
                "Supabase 세션 방식 연결 문자열을 넣으세요. docs/BACKEND_SETUP.md 참고."
            )
        return self.database_url

    def require_r2(self) -> R2Settings:
        if not self.r2.configured:
            raise ConfigError(
                "R2 자격 증명이 없습니다. KHU_R2_ACCOUNT_ID / KHU_R2_ACCESS_KEY_ID / "
                "KHU_R2_SECRET_ACCESS_KEY 를 설정하세요. docs/BACKEND_SETUP.md 참고."
            )
        return self.r2


def load_settings() -> Settings:
    url = os.environ.get("KHU_DATABASE_URL", "").strip()
    # psycopg3 드라이버를 명시한다. 평범한 postgresql:// 도 받아들인다.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)

    return Settings(
        env=os.environ.get("KHU_ENV", "development").strip() or "development",
        database_url=url,
        r2=R2Settings(
            account_id=os.environ.get("KHU_R2_ACCOUNT_ID", "").strip(),
            access_key_id=os.environ.get("KHU_R2_ACCESS_KEY_ID", "").strip(),
            secret_access_key=os.environ.get("KHU_R2_SECRET_ACCESS_KEY", "").strip(),
            bucket_public=os.environ.get("KHU_R2_BUCKET_PUBLIC", "khu-notice-public").strip(),
            bucket_evidence=os.environ.get("KHU_R2_BUCKET_EVIDENCE", "khu-notice-evidence").strip(),
            bucket_backup=os.environ.get("KHU_R2_BUCKET_BACKUP", "khu-notice-backup").strip(),
        ),
        heartbeat_url=os.environ.get("KHU_HEARTBEAT_URL", "").strip(),
        run_budget_seconds=_int("KHU_RUN_BUDGET_SECONDS", 1800),
        http_concurrency=_int("KHU_HTTP_CONCURRENCY", 5),
        http_delay_seconds=_float("KHU_HTTP_DELAY_SECONDS", 2.0),
        http_timeout_seconds=_float("KHU_HTTP_TIMEOUT_SECONDS", 25.0),
        max_response_bytes=_int("KHU_MAX_RESPONSE_BYTES", 8_000_000),
        user_agent=os.environ.get("KHU_USER_AGENT", "").strip()
        or "khu-notice-bot/0.1 (+https://github.com/sihort183-creator/khu)",
        dry_run=_bool("KHU_DRY_RUN", False),
    )


settings = load_settings()
