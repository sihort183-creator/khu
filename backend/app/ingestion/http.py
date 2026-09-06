"""원문 요청 클라이언트.

14절 수집 보안을 실제로 적용한다.
- 등록된 출처의 허용 도메인·포트만 요청한다.
- DNS 결과와 리디렉션 매 단계에서 사설·로컬·클라우드 메타데이터 주소를 거절한다.
- 응답 크기·시간·리디렉션 횟수를 제한한다.
7.3절 속도: 같은 원문 서버 묶음은 동시 1개·간격 2초, 전체 동시 5개.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from app.config import Settings
from app.config import settings as default_settings

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {80, 443}
MAX_REDIRECTS = 5

# 클라우드 메타데이터·링크로컬. 사설 주소와 별도로 명시해 둔다.
BLOCKED_HOSTS = {"metadata.google.internal", "metadata.goog", "localhost"}
BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
)


class FetchError(Exception):
    """원문 요청 실패. kind 로 실패 종류를 구분한다(4절 8항)."""

    def __init__(self, kind: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status

    def __str__(self) -> str:  # pragma: no cover - 메시지 전달용
        return f"[{self.kind}] {super().__str__()}"


class BlockedRequest(FetchError):
    def __init__(self, message: str) -> None:
        super().__init__("blocked_target", message)


@dataclass
class Response:
    url: str
    status: int
    text: str
    content: bytes
    elapsed_ms: int
    headers: dict[str, str] = field(default_factory=dict)


def _host_is_safe(host: str) -> tuple[bool, str]:
    """호스트 이름과 그 DNS 결과가 모두 공인 주소인지 확인한다."""
    lowered = host.lower().rstrip(".")
    if not lowered or lowered in BLOCKED_HOSTS:
        return False, f"차단된 호스트: {host}"

    # 주소를 직접 쓴 경우
    try:
        ip = ipaddress.ip_address(lowered)
    except ValueError:
        ip = None
    if ip is not None and not _ip_is_public(ip):
        return False, f"사설·예약 주소 직접 요청: {host}"

    try:
        infos = socket.getaddrinfo(lowered, None)
    except socket.gaierror as exc:
        return False, f"주소 조회 실패: {host} ({exc})"

    for info in infos:
        addr = info[4][0]
        try:
            resolved = ipaddress.ip_address(addr)
        except ValueError:
            return False, f"해석할 수 없는 주소: {addr}"
        if not _ip_is_public(resolved):
            return False, f"사설·예약 주소로 해석됨: {host} -> {addr}"
    return True, ""


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return False
    if ip.is_link_local:
        return False
    return not any(ip in net for net in BLOCKED_NETWORKS)


def check_url(url: str, *, allowed_hosts: set[str] | None = None) -> str:
    """요청 직전 검사. 통과하면 정규화된 주소를 돌려준다."""
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise BlockedRequest(f"허용하지 않는 통신 방식: {parts.scheme or '(없음)'}")
    if not parts.hostname:
        raise BlockedRequest(f"호스트가 없는 주소: {url}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise BlockedRequest(f"허용하지 않는 포트: {port}")
    if allowed_hosts is not None:
        host = parts.hostname.lower()
        if host not in allowed_hosts:
            raise BlockedRequest(f"출처에 등록되지 않은 도메인: {host}")
    ok, reason = _host_is_safe(parts.hostname)
    if not ok:
        raise BlockedRequest(reason)
    return url


class HostLimiter:
    """원문 서버 묶음별 동시 1개·간격 유지.

    묶는 단위는 둘 중 하나다(7.3절).
      registrable  하위 도메인을 하나로 묶는다. 같은 기반 서버를 쓸 때 안전하다.
                   경희대는 사이트가 125개라 이 방식이면 전부 한 줄로 선다.
      host         하위 도메인마다 따로 센다. 훨씬 빠르지만 서버 부담이 커진다.
                   기반 서버를 공유하지 않는다고 확인했을 때만 쓴다.
    """

    def __init__(self, delay_seconds: float, group_mode: str = "registrable") -> None:
        self._delay = delay_seconds
        self._group_mode = group_mode
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    @staticmethod
    def group_of(url: str, mode: str = "registrable") -> str:
        host = (urlsplit(url).hostname or "").lower()
        if mode == "host":
            return host
        parts = host.split(".")
        if len(parts) >= 3:
            return ".".join(parts[-3:])
        return host

    async def acquire(self, url: str) -> str:
        group = self.group_of(url, self._group_mode)
        lock = self._locks.setdefault(group, asyncio.Lock())
        await lock.acquire()
        wait = self._delay - (time.monotonic() - self._last.get(group, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        return group

    def release(self, group: str) -> None:
        self._last[group] = time.monotonic()
        lock = self._locks.get(group)
        if lock is not None and lock.locked():
            lock.release()


class Fetcher:
    """수집 실행 하나가 공유하는 클라이언트."""

    def __init__(self, cfg: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = cfg or default_settings
        self._limiter = HostLimiter(self.cfg.http_delay_seconds, self.cfg.host_group_mode)
        self._gate = asyncio.Semaphore(self.cfg.http_concurrency)
        self._client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(self.cfg.http_timeout_seconds),
            headers={
                "User-Agent": self.cfg.user_agent,
                "Accept-Language": "ko,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml",
            },
            transport=transport,
        )
        self.requests_made = 0

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def get(self, url: str, *, allowed_hosts: set[str] | None = None) -> Response:
        return await self._request("GET", url, allowed_hosts=allowed_hosts)

    async def post_form(
        self, url: str, data: dict[str, str], *, allowed_hosts: set[str] | None = None
    ) -> Response:
        return await self._request("POST", url, data=data, allowed_hosts=allowed_hosts)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        allowed_hosts: set[str] | None = None,
    ) -> Response:
        # DNS 조회는 동기 함수이므로 다른 출처의 수집을 막지 않도록 분리한다.
        current = await asyncio.to_thread(check_url, url, allowed_hosts=allowed_hosts)
        started = time.monotonic()

        async with self._gate:
            for hop in range(MAX_REDIRECTS + 1):
                group = await self._limiter.acquire(current)
                try:
                    try:
                        resp = await self._client.request(
                            method if hop == 0 else "GET",
                            current,
                            data=data if hop == 0 else None,
                        )
                    except httpx.TimeoutException as exc:
                        raise FetchError("timeout", f"시간 초과: {current} ({exc})") from exc
                    except httpx.HTTPError as exc:
                        raise FetchError("connection", f"연결 실패: {current} ({exc})") from exc
                    finally:
                        self.requests_made += 1
                finally:
                    self._limiter.release(group)

                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError("bad_redirect", f"위치 없는 리디렉션: {current}")
                    current = await asyncio.to_thread(
                        check_url, str(resp.url.join(location)), allowed_hosts=allowed_hosts
                    )
                    data = None
                    continue

                return self._finish(resp, current, started)

        raise FetchError("too_many_redirects", f"리디렉션이 너무 많습니다: {url}")

    def _finish(self, resp: httpx.Response, url: str, started: float) -> Response:
        body = resp.content
        if len(body) > self.cfg.max_response_bytes:
            raise FetchError(
                "too_large",
                f"응답이 상한을 넘었습니다: {len(body)} 바이트 > {self.cfg.max_response_bytes}",
                status=resp.status_code,
            )

        if resp.status_code == 429:
            raise FetchError("rate_limited", f"요청 제한 응답: {url}", status=429)
        if resp.status_code in (401, 403):
            raise FetchError("access_denied", f"접근이 거부되었습니다: {url}", status=resp.status_code)
        if resp.status_code == 404:
            raise FetchError("not_found", f"원문을 찾을 수 없습니다: {url}", status=404)
        if resp.status_code >= 500:
            raise FetchError("server_error", f"원문 서버 오류 {resp.status_code}: {url}", status=resp.status_code)
        if resp.status_code >= 400:
            raise FetchError("http_error", f"요청 실패 {resp.status_code}: {url}", status=resp.status_code)

        return Response(
            url=url,
            status=resp.status_code,
            text=_decode(resp, body),
            content=body,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            headers={k.lower(): v for k, v in resp.headers.items()},
        )


def _decode(resp: httpx.Response, body: bytes) -> str:
    """한국어 게시판의 문자 인코딩을 확정한다(8.1절).

    선언된 인코딩을 먼저 믿되 실제로 해독되지 않으면 EUC-KR 계열을 시도한다.
    """
    candidates: list[str] = []
    declared = (resp.charset_encoding or "").lower()
    if declared:
        candidates.append(declared)
    head = body[:2048].decode("ascii", "ignore").lower()
    for token in ("euc-kr", "ks_c_5601-1987", "cp949", "utf-8"):
        if token in head and token not in candidates:
            candidates.append(token)
    for fallback in ("utf-8", "cp949", "euc-kr"):
        if fallback not in candidates:
            candidates.append(fallback)

    for enc in candidates:
        name = {"ks_c_5601-1987": "cp949", "euc-kr": "cp949"}.get(enc, enc)
        try:
            return body.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", "replace")
