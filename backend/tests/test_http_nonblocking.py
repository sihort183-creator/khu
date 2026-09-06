"""DNS가 느려도 병렬 수집은 진행하고 모든 요청의 주소 검사는 유지한다."""

import asyncio
import socket
import threading

import httpx
import pytest

from app.ingestion.http import BlockedRequest, Fetcher


def public_address():
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]


@pytest.mark.asyncio
@pytest.mark.parametrize("redirect", [False, True])
async def test_slow_dns_does_not_block_other_coroutine(settings, monkeypatch, redirect):
    entered = threading.Event()
    release = threading.Event()
    slow_host = "redirect.example" if redirect else "notice.example"

    def resolve(host, port):
        if host == slow_host:
            entered.set()
            # 다른 코루틴이 실행되어야 해제된다. 동기 호출로 회귀하면 실패한다.
            assert release.wait(2), "DNS 조회가 이벤트 루프를 막았습니다"
        return public_address()

    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    def response(request):
        if redirect and request.url.host == "notice.example":
            return httpx.Response(302, headers={"location": "https://redirect.example/post"})
        return httpx.Response(200, text="공지")

    async def independent_work():
        while not entered.is_set():
            await asyncio.sleep(0.001)
        release.set()

    async with Fetcher(settings, transport=httpx.MockTransport(response)) as fetcher:
        result, _ = await asyncio.wait_for(
            asyncio.gather(
                fetcher.get("https://notice.example/post"), independent_work()
            ),
            timeout=5,
        )
        assert result.text == "공지"
        assert fetcher.requests_made == (2 if redirect else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("private_dns", [False, True])
async def test_redirect_still_blocks_unregistered_or_private_target(
    settings, monkeypatch, private_dns
):
    def resolve(host, port):
        if private_dns and host == "redirect.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
        return public_address()

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    requested = []

    def response(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://redirect.example/post"})

    allowed_hosts = {"notice.example"}
    if private_dns:
        allowed_hosts.add("redirect.example")
    async with Fetcher(settings, transport=httpx.MockTransport(response)) as fetcher:
        with pytest.raises(BlockedRequest) as error:
            await fetcher.get("https://notice.example/post", allowed_hosts=allowed_hosts)
        assert error.value.kind == "blocked_target"
        assert requested == ["https://notice.example/post"]
