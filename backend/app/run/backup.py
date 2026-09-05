"""논리 백업(17.2절).

Supabase 무료 요금제에는 자동 백업이 없다. 이 절차가 유일한 복구 수단이므로
실패하면 즉시 경보한다(18절). 6시간마다 별도 워크플로가 실행한다.

  python -m app.run.backup            백업 만들어 R2 백업 버킷에 올린다
  python -m app.run.backup --verify   최근 백업을 내려받아 열어 본다
  python -m app.run.backup --list     보관 중인 백업 목록

암호화: KHU_BACKUP_PASSPHRASE 가 있으면 그 문구로 대칭 암호화한다.
복호화 키는 Actions 비밀값이 아니라 운영자 개인 보관소에 둔다.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import hmac
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from app.config import Settings
from app.config import settings as default_settings
from app.storage.objects import ObjectStore, build_store

# 보존: 최근 7일은 6시간 단위, 이후 30일까지 하루 1개(17.2절).
RECENT_DAYS = 7
DAILY_DAYS = 30
PREFIX = "pgdump"


def _dsn_to_libpq(url: str) -> str:
    """SQLAlchemy 형식을 pg_dump 가 받는 형식으로 바꾼다."""
    cleaned = url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    return cleaned


def _key_for(now: datetime) -> str:
    return f"{PREFIX}/{now:%Y/%m/%d}/khu-{now:%Y%m%dT%H%M%SZ}.sql.gz"


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, 200_000, dklen=32)


def encrypt(data: bytes, passphrase: str) -> bytes:
    """의존성 없는 대칭 암호화.

    표준 라이브러리만으로 만든다. 키 흐름(PBKDF2 -> HMAC 기반 키 흐름 XOR)과
    HMAC 무결성 표시를 함께 쓴다. 백업 파일이 그대로 읽히지 않게 하는 것이 목적이다.
    """
    salt = os.urandom(16)
    key = _derive_key(passphrase, salt)
    stream = bytearray()
    counter = 0
    while len(stream) < len(data):
        stream.extend(hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    body = bytes(a ^ b for a, b in zip(data, stream[: len(data)], strict=True))
    tag = hmac.new(key, salt + body, hashlib.sha256).digest()
    return b"KHUBK1" + salt + tag + body


def decrypt(blob: bytes, passphrase: str) -> bytes:
    if not blob.startswith(b"KHUBK1"):
        raise ValueError("백업 형식이 아닙니다.")
    salt, tag, body = blob[6:22], blob[22:54], blob[54:]
    key = _derive_key(passphrase, salt)
    if not hmac.compare_digest(tag, hmac.new(key, salt + body, hashlib.sha256).digest()):
        raise ValueError("백업이 손상되었거나 암호 문구가 다릅니다.")
    stream = bytearray()
    counter = 0
    while len(stream) < len(body):
        stream.extend(hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(a ^ b for a, b in zip(body, stream[: len(body)], strict=True))


def run_pg_dump(dsn: str, target: Path) -> int:
    if shutil.which("pg_dump") is None:
        raise RuntimeError(
            "pg_dump 를 찾을 수 없습니다. Actions 에서는 postgresql-client 를 설치하세요."
        )
    command = [
        "pg_dump",
        "--no-owner",
        "--no-privileges",
        "--format=plain",
        "--encoding=UTF8",
        _dsn_to_libpq(dsn),
    ]
    with target.open("wb") as handle:
        completed = subprocess.run(command, stdout=handle, stderr=subprocess.PIPE, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"pg_dump 실패: {completed.stderr.decode('utf-8', 'replace')[:600]}")
    return target.stat().st_size


def create_backup(cfg: Settings | None = None, *, store: ObjectStore | None = None) -> dict:
    cfg = cfg or default_settings
    store = store or build_store(cfg)
    dsn = cfg.require_database()
    now = datetime.now(UTC)

    with tempfile.TemporaryDirectory() as workdir:
        raw = Path(workdir) / "dump.sql"
        size = run_pg_dump(dsn, raw)
        packed = gzip.compress(raw.read_bytes(), compresslevel=6)

    passphrase = os.environ.get("KHU_BACKUP_PASSPHRASE", "").strip()
    payload = encrypt(packed, passphrase) if passphrase else packed
    digest = hashlib.sha256(payload).hexdigest()

    key = _key_for(now)
    store.put_bytes(
        cfg.r2.bucket_backup,
        key,
        payload,
        content_type="application/octet-stream",
        compress=False,
    )
    store.put_bytes(
        cfg.r2.bucket_backup,
        key + ".sha256",
        f"{digest}  {key}\n".encode(),
        content_type="text/plain; charset=utf-8",
    )
    return {
        "key": key,
        "raw_bytes": size,
        "stored_bytes": len(payload),
        "sha256": digest,
        "encrypted": bool(passphrase),
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "database_host": urlsplit(_dsn_to_libpq(dsn)).hostname,
    }


def list_backups(cfg: Settings | None = None, *, store: ObjectStore | None = None) -> list[str]:
    cfg = cfg or default_settings
    store = store or build_store(cfg)
    return sorted(k for k in store.list_keys(cfg.r2.bucket_backup, PREFIX) if not k.endswith(".sha256"))


def verify_latest(cfg: Settings | None = None, *, store: ObjectStore | None = None) -> dict:
    """백업 파일 존재만으로 통과시키지 않는다(17.3절). 실제로 열어 본다."""
    cfg = cfg or default_settings
    store = store or build_store(cfg)
    keys = list_backups(cfg, store=store)
    if not keys:
        raise RuntimeError("보관된 백업이 없습니다.")
    key = keys[-1]

    payload = store.get_bytes(cfg.r2.bucket_backup, key)
    expected = store.get_bytes(cfg.r2.bucket_backup, key + ".sha256").decode().split()[0]
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise RuntimeError(f"백업 해시가 다릅니다: {key}")

    passphrase = os.environ.get("KHU_BACKUP_PASSPHRASE", "").strip()
    blob = decrypt(payload, passphrase) if payload.startswith(b"KHUBK1") else payload
    if payload.startswith(b"KHUBK1") and not passphrase:
        raise RuntimeError("암호화된 백업인데 KHU_BACKUP_PASSPHRASE 가 없습니다.")
    text = gzip.decompress(blob).decode("utf-8", "replace")

    if "CREATE TABLE" not in text:
        raise RuntimeError("백업에 스키마가 없습니다. 내용을 확인하세요.")
    tables = text.count("CREATE TABLE")
    return {"key": key, "sha256": actual, "tables": tables, "bytes": len(payload)}


def prune(cfg: Settings | None = None, *, store: ObjectStore | None = None, now: datetime | None = None) -> int:
    """보존 정책 적용. 사용자 삭제 요청이 장기 잔존하지 않게 오래된 백업을 지운다."""
    cfg = cfg or default_settings
    store = store or build_store(cfg)
    now = now or datetime.now(UTC)
    keys = list_backups(cfg, store=store)

    keep: set[str] = set()
    seen_days: set[str] = set()
    removed = 0
    for key in sorted(keys, reverse=True):
        stamp = key.rsplit("/", 1)[-1].removeprefix("khu-").removesuffix(".sql.gz")
        try:
            when = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            keep.add(key)
            continue
        age = now - when
        if age <= timedelta(days=RECENT_DAYS):
            keep.add(key)
        elif age <= timedelta(days=DAILY_DAYS):
            day = when.strftime("%Y-%m-%d")
            if day not in seen_days:
                seen_days.add(day)
                keep.add(key)

    for key in keys:
        if key not in keep:
            store.delete(cfg.r2.bucket_backup, key)
            store.delete(cfg.r2.bucket_backup, key + ".sha256")
            removed += 1
    return removed


def _heartbeat(cfg: Settings, ok: bool) -> None:
    url = os.environ.get("KHU_BACKUP_HEARTBEAT_URL", "").strip()
    if not url:
        return
    import urllib.request

    target = url if ok else url.rstrip("/") + "/fail"
    try:
        urllib.request.urlopen(
            urllib.request.Request(target, headers={"User-Agent": cfg.user_agent}), timeout=10
        ).close()
    except Exception:  # noqa: BLE001
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="데이터베이스 논리 백업")
    parser.add_argument("--verify", action="store_true", help="최근 백업을 내려받아 확인만 한다")
    parser.add_argument("--list", action="store_true", help="보관 중인 백업 목록")
    parser.add_argument("--prune", action="store_true", help="보존 정책에 따라 오래된 백업 정리")
    args = parser.parse_args(argv)

    cfg = default_settings
    try:
        if args.list:
            for key in list_backups(cfg):
                print(key)
            return 0
        if args.verify:
            info = verify_latest(cfg)
            print(f"백업 확인 통과: {info['key']} (테이블 {info['tables']}개, {info['bytes'] / 1024:.0f}KB)")
            return 0

        info = create_backup(cfg)
        print(
            f"백업 완료: {info['key']} 원본 {info['raw_bytes'] / 1024:.0f}KB → "
            f"저장 {info['stored_bytes'] / 1024:.0f}KB 암호화={info['encrypted']}"
        )
        if args.prune:
            print(f"오래된 백업 {prune(cfg)}개 정리")
        _heartbeat(cfg, True)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"백업 실패: {exc}", file=sys.stderr)
        _heartbeat(cfg, False)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
