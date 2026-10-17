"""扫码登录：二维码获取、状态轮询与 Cookie 换取。"""

from __future__ import annotations

import httpx
import pytest

from baidu_pan.errors import QrLoginError
from baidu_pan.qrlogin import QrLoginSession


def make_session(
    routes: dict[str, httpx.Response | callable], **kwargs
) -> QrLoginSession:
    async def handler(request: httpx.Request) -> httpx.Response:
        route = routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, text="not found")
        if callable(route):
            result = route(request)
            if hasattr(result, "__await__"):
                result = await result
            return result
        return route

    return QrLoginSession(transport=httpx.MockTransport(handler), **kwargs)


async def test_create_returns_qr_png() -> None:
    session = make_session(
        {
            "/v2/api/getqrcode": httpx.Response(
                200, json={"errno": 0, "sign": "sign-1", "imgurl": "https://passport.baidu.com/qr.png"}
            ),
            "/qr.png": httpx.Response(200, content=b"\x89PNG\r\n\x1a\n"),
        }
    )
    async with session:
        qr = await session.create()
    assert qr.sign == "sign-1"
    assert qr.image.startswith(b"\x89PNG")


async def test_create_rejects_missing_fields() -> None:
    session = make_session({"/v2/api/getqrcode": httpx.Response(200, json={"errno": 0})})
    async with session:
        with pytest.raises(QrLoginError):
            await session.create()


async def test_poll_status_mapping() -> None:
    responses = {
        "waiting": '( {"errno":0,"channel_v":"{\\"status\\":0}"} )',
        "scanned": '( {"errno":0,"channel_v":"{\\"status\\":1}"} )',
        "confirmed": '( {"errno":0,"channel_v":"{\\"status\\":1,\\"v\\":\\"token-1\\"}"} )',
        "expired": '( {"errno":0,"channel_v":"{\\"status\\":9}"} )',
    }

    async def unicast(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=responses[current[0]])

    current = ["waiting"]
    session = make_session({"/channel/unicast": unicast})
    async with session:
        assert (await session.poll("sign")).status == "waiting"
        current[0] = "scanned"
        assert (await session.poll("sign")).status == "scanned"
        current[0] = "confirmed"
        poll = await session.poll("sign")
        assert poll.status == "confirmed"
        assert poll.token == "token-1"
        current[0] = "expired"
        assert (await session.poll("sign")).status == "expired"


async def test_confirm_extracts_cookies() -> None:
    def login_redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={
                "Location": "https://pan.baidu.com/",
                "Set-Cookie": "BDUSS=bduss-value; Domain=.baidu.com; Path=/",
            },
        )

    session = make_session(
        {
            "/v3/login/main/qrbdusslogin": login_redirect,
            "/disk/main": httpx.Response(200, text="ok"),
        }
    )
    async with session:
        credentials = await session.confirm("token-1")
    assert credentials.bduss == "bduss-value"
    assert "BDUSS=bduss-value" in credentials.cookies


async def test_confirm_without_bduss_raises() -> None:
    session = make_session(
        {
            "/v3/login/main/qrbdusslogin": httpx.Response(302, headers={"Location": "https://pan.baidu.com/"}),
            "/disk/main": httpx.Response(200, text="ok"),
        }
    )
    async with session:
        with pytest.raises(QrLoginError):
            await session.confirm("token-1")


async def test_wait_for_scan_timeout() -> None:
    session = make_session(
        {"/channel/unicast": httpx.Response(200, text='({"errno":0,"channel_v":"{\\"status\\":0}"})')},
        poll_interval=0,
        poll_attempts=2,
    )
    async with session:
        with pytest.raises(QrLoginError):
            await session.wait_for_scan("sign-1")
