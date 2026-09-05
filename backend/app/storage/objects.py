"""객체 저장(Cloudflare R2, S3 호환).

버킷 분리(15.2절):
- evidence: 원문 HTML 증거. 비공개.
- public  : 조회용 정적 JSON. Worker 바인딩으로만 읽는다.
- backup  : 암호화 논리 백업. 쓰기 전용 키를 쓴다.

자격 증명이 없으면 LocalObjectStore 로 떨어져 로컬·검사에서 그대로 동작한다.
"""

from __future__ import annotations

import gzip
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.config import R2Settings, Settings
from app.config import settings as default_settings


@dataclass(frozen=True)
class PutResult:
    bucket: str
    key: str
    size: int
    compressed: bool


class ObjectStore(Protocol):
    def put_bytes(
        self, bucket: str, key: str, data: bytes, *, content_type: str, compress: bool = False
    ) -> PutResult: ...

    def get_bytes(self, bucket: str, key: str) -> bytes: ...

    def delete(self, bucket: str, key: str) -> None: ...

    def list_keys(self, bucket: str, prefix: str) -> list[str]: ...


def _maybe_gzip(data: bytes, compress: bool) -> tuple[bytes, bool]:
    if not compress:
        return data, False
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as handle:
        handle.write(data)
    packed = buffer.getvalue()
    # 압축이 도움이 되지 않으면 원본을 쓴다.
    return (packed, True) if len(packed) < len(data) else (data, False)


class LocalObjectStore:
    """로컬·검사용. 파일 시스템에 같은 키 구조로 쌓는다."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, bucket: str, key: str) -> Path:
        return self.root / bucket / key

    def put_bytes(
        self, bucket: str, key: str, data: bytes, *, content_type: str, compress: bool = False
    ) -> PutResult:
        payload, compressed = _maybe_gzip(data, compress)
        target = self._path(bucket, key + (".gz" if compressed else ""))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return PutResult(bucket=bucket, key=key, size=len(payload), compressed=compressed)

    def get_bytes(self, bucket: str, key: str) -> bytes:
        target = self._path(bucket, key)
        if target.exists():
            return target.read_bytes()
        packed = self._path(bucket, key + ".gz")
        if packed.exists():
            return gzip.decompress(packed.read_bytes())
        raise FileNotFoundError(f"{bucket}/{key}")

    def delete(self, bucket: str, key: str) -> None:
        for candidate in (self._path(bucket, key), self._path(bucket, key + ".gz")):
            if candidate.exists():
                candidate.unlink()

    def list_keys(self, bucket: str, prefix: str) -> list[str]:
        base = self.root / bucket
        if not base.exists():
            return []
        out: list[str] = []
        for path in base.rglob("*"):
            if path.is_file():
                key = path.relative_to(base).as_posix()
                key = key[:-3] if key.endswith(".gz") else key
                if key.startswith(prefix):
                    out.append(key)
        return sorted(out)


class R2ObjectStore:
    """운영용. boto3 S3 클라이언트를 R2 엔드포인트로 쓴다."""

    def __init__(self, cfg: R2Settings) -> None:
        import boto3
        from botocore.config import Config

        self.cfg = cfg
        self._client = boto3.client(
            "s3",
            endpoint_url=cfg.endpoint_url,
            aws_access_key_id=cfg.access_key_id,
            aws_secret_access_key=cfg.secret_access_key,
            region_name="auto",
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=15,
                read_timeout=60,
                signature_version="s3v4",
            ),
        )

    def put_bytes(
        self, bucket: str, key: str, data: bytes, *, content_type: str, compress: bool = False
    ) -> PutResult:
        payload, compressed = _maybe_gzip(data, compress)
        extra = {"ContentType": content_type}
        if compressed:
            extra["ContentEncoding"] = "gzip"
        self._client.put_object(Bucket=bucket, Key=key, Body=payload, **extra)
        return PutResult(bucket=bucket, key=key, size=len(payload), compressed=compressed)

    def get_bytes(self, bucket: str, key: str) -> bytes:
        response = self._client.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read()
        if response.get("ContentEncoding") == "gzip":
            return gzip.decompress(body)
        return body

    def delete(self, bucket: str, key: str) -> None:
        self._client.delete_object(Bucket=bucket, Key=key)

    def list_keys(self, bucket: str, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kwargs["ContinuationToken"] = token
            page = self._client.list_objects_v2(**kwargs)
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        return keys


def build_store(cfg: Settings | None = None, *, local_root: Path | None = None) -> ObjectStore:
    cfg = cfg or default_settings
    if cfg.r2.configured:
        return R2ObjectStore(cfg.r2)
    root = local_root or (Path.cwd() / ".localstore")
    return LocalObjectStore(root)


def evidence_key(source_id: str, external_id: str, content_hash: str) -> str:
    """원문 증거 경로. 내용 해시가 같으면 파일을 재사용한다(6.2절)."""
    return f"raw/{source_id}/{external_id}/{content_hash[:16]}.html"
