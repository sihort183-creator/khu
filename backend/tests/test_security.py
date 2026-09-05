"""수집 보안 검사(14절·21.1절 보안 항목).

내부 주소 접근, 등록되지 않은 도메인, 과대 응답, 리디렉션을 실제로 막아야 한다.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import R2Settings, Settings
from app.ingestion.http import BlockedRequest, Fetcher, FetchError, HostLimiter, check_url

ALLOWED = {"www.khu.ac.kr"}


def _settings(**kw) -> Settings:
    base = dict(
        env="test",
        database_url="sqlite://",
        r2=R2Settings(),
        http_concurrency=2,
        http_delay_seconds=0.0,
        http_timeout_seconds=5.0,
        max_response_bytes=1000,
    )
    base.update(kw)
    return Settings(**base)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8080/",
        "https://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "https://metadata.google.internal/",
    ],
)
def test_internal_addresses_are_refused(url):
    with pytest.raises(BlockedRequest):
        check_url(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://khu.ac.kr/x", "gopher://x/"])
def test_non_web_schemes_are_refused(url):
    with pytest.raises(BlockedRequest):
        check_url(url)


def test_non_standard_ports_are_refused():
    with pytest.raises(BlockedRequest):
        check_url("https://www.khu.ac.kr:9000/x")


def test_unregistered_domain_is_refused():
    with pytest.raises(BlockedRequest):
        check_url("https://evil.example.com/x", allowed_hosts=ALLOWED)


def test_registered_domain_passes():
    assert check_url("https://www.khu.ac.kr/kor/x", allowed_hosts=ALLOWED)


def test_oversized_response_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="가" * 5000)

    async def run():
        async with Fetcher(_settings(), transport=httpx.MockTransport(handler)) as fetcher:
            with pytest.raises(FetchError) as excinfo:
                await fetcher.get("https://www.khu.ac.kr/big", allowed_hosts=ALLOWED)
            return excinfo.value

    assert asyncio.run(run()).kind == "too_large"


def test_redirect_to_internal_address_is_blocked():
    """리디렉션 매 단계에도 같은 검사를 적용한다(14절)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/meta"})
        return httpx.Response(200, text="ok")

    async def run():
        async with Fetcher(_settings(), transport=httpx.MockTransport(handler)) as fetcher:
            with pytest.raises(BlockedRequest):
                await fetcher.get("https://www.khu.ac.kr/start", allowed_hosts=ALLOWED)

    asyncio.run(run())


def test_redirect_off_allowed_domain_is_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://evil.example.com/x"})
        return httpx.Response(200, text="ok")

    async def run():
        async with Fetcher(_settings(), transport=httpx.MockTransport(handler)) as fetcher:
            with pytest.raises(BlockedRequest):
                await fetcher.get("https://www.khu.ac.kr/start", allowed_hosts=ALLOWED)

    asyncio.run(run())


def test_redirect_loop_stops():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://www.khu.ac.kr/loop"})

    async def run():
        async with Fetcher(_settings(), transport=httpx.MockTransport(handler)) as fetcher:
            with pytest.raises(FetchError) as excinfo:
                await fetcher.get("https://www.khu.ac.kr/loop", allowed_hosts=ALLOWED)
            return excinfo.value

    assert asyncio.run(run()).kind == "too_many_redirects"


@pytest.mark.parametrize(
    "status,kind",
    [(429, "rate_limited"), (403, "access_denied"), (404, "not_found"), (500, "server_error")],
)
def test_error_kinds_are_distinguished(status, kind):
    """4절 8항: 실패 종류를 구별해야 감속·격리·재시도를 다르게 할 수 있다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="x")

    async def run():
        async with Fetcher(_settings(), transport=httpx.MockTransport(handler)) as fetcher:
            with pytest.raises(FetchError) as excinfo:
                await fetcher.get("https://www.khu.ac.kr/x", allowed_hosts=ALLOWED)
            return excinfo.value

    assert asyncio.run(run()).kind == kind


def test_host_group_shares_rate_limit_across_subdomains():
    """7.3절: 하위 도메인이 같은 기반 서버를 쓰면 묶어서 제한한다."""
    assert HostLimiter.group_of("https://cs.khu.ac.kr/a") == HostLimiter.group_of(
        "https://ce.khu.ac.kr/b"
    )
    assert HostLimiter.group_of("https://cs.khu.ac.kr/a") != HostLimiter.group_of(
        "https://example.org/b"
    )


def test_encoding_is_detected_for_korean_pages():
    """8.1절: 한국어 인코딩을 확정한다. 깨진 글자로 저장하지 않는다."""
    body = "<html><head><meta charset='euc-kr'></head><body>공지사항</body></html>".encode("cp949")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    async def run():
        async with Fetcher(_settings(max_response_bytes=100000), transport=httpx.MockTransport(handler)) as fetcher:
            return await fetcher.get("https://www.khu.ac.kr/x", allowed_hosts=ALLOWED)

    assert "공지사항" in asyncio.run(run()).text


def test_user_agent_identifies_the_service():
    settings = _settings()
    assert "khu-notice-bot" in settings.user_agent
    assert "github.com" in settings.user_agent
