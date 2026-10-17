"""百度 Passport 扫码登录：申请二维码、轮询扫码状态、换取 Cookie。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import QrLoginError

GETQRCODE_URL = "https://passport.baidu.com/v2/api/getqrcode"
UNICAST_URL = "https://passport.baidu.com/channel/unicast"
QRBDUSSLOGIN_URL = "https://passport.baidu.com/v3/login/main/qrbdusslogin"
PAN_MAIN = "https://pan.baidu.com/disk/main"

_JSONP_RE = re.compile(r"^[^(]*\((.*)\)\s*;?\s*$", re.DOTALL)


@dataclass
class QrCode:
    """二维码信息：``image`` 为 PNG 原始字节。"""

    sign: str
    image: bytes
    image_url: str = ""


@dataclass
class QrPoll:
    """一次轮询结果。"""

    status: str  # waiting | scanned | confirmed | expired
    token: str = ""
    message: str = ""


@dataclass
class QrCredentials:
    cookies: str
    bduss: str
    stoken: str = ""


class QrLoginSession:
    """一次扫码登录会话；调用方负责 ``aclose``。"""

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        poll_interval: float = 3.0,
        poll_attempts: int = 60,
        transport: httpx.AsyncBaseTransport | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._timeout = timeout
        self.poll_interval = poll_interval
        self.poll_attempts = poll_attempts
        self._logger = logger or logging.getLogger(__name__)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            transport=transport,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://pan.baidu.com/",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "QrLoginSession":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    async def create(self) -> QrCode:
        """申请二维码并下载 PNG。"""

        try:
            response = await self._client.get(
                GETQRCODE_URL,
                params={"lp": "pc", "qrloginfrom": "pc"},
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise QrLoginError("获取百度登录二维码失败，请检查网络。", detail=str(exc)) from exc
        except ValueError as exc:
            raise QrLoginError("百度登录二维码接口返回格式异常。", detail=str(exc)) from exc
        if not isinstance(data, dict):
            raise QrLoginError("百度登录二维码接口返回格式异常。")
        sign = str(data.get("sign") or "").strip()
        imgurl = str(data.get("imgurl") or data.get("img") or "").strip()
        if not sign or not imgurl:
            raise QrLoginError("百度登录二维码接口未返回有效二维码。")
        if imgurl.startswith("//"):
            imgurl = f"https:{imgurl}"
        elif not imgurl.startswith(("http://", "https://")):
            imgurl = f"https://{imgurl}"

        try:
            response = await self._client.get(imgurl)
            response.raise_for_status()
            image = response.content
        except httpx.HTTPError as exc:
            raise QrLoginError("下载登录二维码图片失败。", detail=str(exc)) from exc
        if not image:
            raise QrLoginError("登录二维码图片为空。")
        return QrCode(sign=sign, image=image, image_url=imgurl)

    async def poll(self, sign: str) -> QrPoll:
        """轮询一次扫码状态。"""

        params = {
            "channel_id": sign,
            "gid": uuid.uuid4().hex.upper(),
            "tpl": "mm",
            "_sdkFrom": "1",
            "apiver": "v3",
            "tt": str(int(time.time() * 1000)),
            "callback": "",
        }
        try:
            response = await self._client.get(UNICAST_URL, params=params)
            response.raise_for_status()
            payload = _parse_jsonp(response.text)
        except httpx.HTTPError as exc:
            raise QrLoginError("轮询扫码状态失败，请检查网络。", detail=str(exc)) from exc
        except ValueError as exc:
            raise QrLoginError("扫码状态返回格式异常。", detail=str(exc)) from exc

        channel: Any = payload.get("channel_v") if isinstance(payload, dict) else None
        if isinstance(channel, str):
            try:
                channel = json.loads(channel)
            except ValueError:
                channel = None
        if not isinstance(channel, dict):
            channel = payload if isinstance(payload, dict) else {}
        try:
            status = int(channel.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        token = str(channel.get("v") or payload.get("v") or "").strip()

        if token and status in {1, 2, 3}:
            return QrPoll(status="confirmed", token=token)
        if status == 0:
            return QrPoll(status="waiting")
        if status == 1:
            return QrPoll(status="scanned")
        return QrPoll(status="expired", message=str(channel.get("msg") or ""))

    async def confirm(self, token: str) -> QrCredentials:
        """用轮询得到的 token 换取 BDUSS/STOKEN 等 Cookie。"""

        params = {
            "bduss": token,
            "u": "",
            "loginVersion": "v4",
            "qrcode": "1",
            "tpl": "mm",
            "apiver": "v3",
        }
        try:
            response = await self._client.get(QRBDUSSLOGIN_URL, params=params, follow_redirects=False)
            if response.status_code >= 400:
                raise QrLoginError("百度扫码确认失败，请重试。")
            await self._client.get(PAN_MAIN)
        except httpx.HTTPError as exc:
            raise QrLoginError("百度扫码确认失败，请重试。", detail=str(exc)) from exc

        cookies: list[str] = []
        values: dict[str, str] = {}
        for cookie in self._client.cookies.jar:
            values[cookie.name] = cookie.value
            cookies.append(f"{cookie.name}={cookie.value}")
        bduss = values.get("BDUSS", "")
        if not bduss:
            raise QrLoginError("扫码已确认，但未获取到 BDUSS，请重试或改用 /pan bduss。")
        return QrCredentials(cookies="; ".join(cookies), bduss=bduss, stoken=values.get("STOKEN", ""))

    async def wait_for_scan(self, sign: str) -> tuple[QrCredentials, bool]:
        """阻塞轮询直到确认/过期；返回 ``(凭据, 是否中途扫过码)``。"""

        scanned = False
        for _ in range(self.poll_attempts):
            await asyncio.sleep(self.poll_interval)
            result = await self.poll(sign)
            if result.status == "waiting":
                continue
            if result.status == "scanned":
                scanned = True
                continue
            if result.status == "confirmed":
                credentials = await self.confirm(result.token)
                return credentials, scanned
            raise QrLoginError("登录二维码已失效或被取消，请重新执行 /pan qrlogin。")
        raise QrLoginError("登录二维码等待超时，请重新执行 /pan qrlogin。")


def _parse_jsonp(text: str) -> dict[str, Any]:
    stripped = text.strip()
    match = _JSONP_RE.match(stripped)
    if match is not None:
        stripped = match.group(1)
    data = json.loads(stripped) if stripped else {}
    if not isinstance(data, dict):
        raise ValueError("JSON 根节点不是对象")
    return data


__all__ = [
    "GETQRCODE_URL",
    "PAN_MAIN",
    "QRBDUSSLOGIN_URL",
    "UNICAST_URL",
    "QrCode",
    "QrCredentials",
    "QrLoginSession",
    "QrPoll",
]
