"""百度网盘分享协议：解析链接、校验提取码、浏览目录树。

实现只依赖公开 Web 接口，与 AstrBot 原插件保持相同的请求顺序；
所有网络异常都会转换为 ``BaiduPanError`` 子类，供上游统一转成中文提示。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .errors import (
    PasswordInvalidError,
    PasswordRequiredError,
    ShareNotFoundError,
    ShareParseError,
    TransferError,
)
from .models import ShareInfo, ShareNode, ShareTree
from .utils import format_size

PAN_MAIN = "https://pan.baidu.com/disk/main"
SHORTURL_API = "https://pan.baidu.com/api/shorturlinfo"
TEMPLATE_API = "https://pan.baidu.com/api/gettemplatevariable"
VERIFY_API = "https://pan.baidu.com/share/verify"
LIST_API = "https://pan.baidu.com/share/list"
TRANSFER_API = "https://pan.baidu.com/share/transfer"
SHARE_PAGE = "https://pan.baidu.com/s/{surl}"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_SURL_RE = re.compile(r"pan\.baidu\.com/s/([A-Za-z0-9_-]+)", re.IGNORECASE)
_PWD_RE = re.compile(r"(?:pwd|password|提取码)\s*[:=]?\s*([A-Za-z0-9]{4,6})", re.IGNORECASE)
_BARE_SURL_RE = re.compile(r"^(?:s/)?(1[A-Za-z0-9_-]{5,})$")


# --------------------------------------------------------------------------- 链接解析


def parse_share_input(text: str, password: str = "") -> tuple[str, str]:
    """从用户输入中解析分享 surl 与提取码。

    Returns:
        ``(surl, pwd)``，``surl`` 可能是 ``1AbCd`` 形式；提取码未提供时为空串。
    """

    raw = str(text or "").strip()
    if not raw:
        raise ShareParseError("请提供百度网盘分享链接或 surl。")

    match = _SURL_RE.search(raw)
    if match:
        surl = match.group(1)
    else:
        token = raw.split()[0].strip().rstrip("/")
        bare = _BARE_SURL_RE.match(token)
        if bare is None:
            raise ShareParseError("无法识别分享链接，请使用 pan.baidu.com/s/... 形式的链接。")
        surl = bare.group(1)

    pwd = str(password or "").strip()
    if not pwd:
        pwd_match = _PWD_RE.search(raw)
        if pwd_match:
            pwd = pwd_match.group(1)
    if not pwd:
        # 兼容「链接 052x」这种裸提取码写法：在去掉 URL 后的剩余 token 中查找。
        remainder = raw.replace(match.group(0), " ") if match else raw.replace(surl, " ")
        for token in remainder.replace("，", " ").replace(",", " ").split():
            candidate = token.strip(";；:：=提取码")
            if candidate != surl and re.fullmatch(r"[A-Za-z0-9]{4,6}", candidate):
                pwd = candidate
                break
    return surl, pwd


def shorturl_for_info(surl: str) -> str:
    """shorturlinfo 接口要求 surl 带前导 ``1``。"""

    base = surl.strip().rstrip("/")
    return base if base.startswith("1") else f"1{base}"


def shorturl_for_list(surl: str) -> str:
    """share/list 接口要求去掉 surl 的前导 ``1``。"""

    base = surl.strip().rstrip("/")
    return base[1:] if base.startswith("1") else base


# --------------------------------------------------------------------------- 浏览会话


@dataclass
class _CacheEntry:
    tree: ShareTree
    created_at: float = field(default_factory=time.monotonic)


class ShareSessionCache:
    """按 stream_id 缓存「当前分享」目录树，带 TTL。"""

    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}

    def get(self, key: str, ttl_seconds: int) -> ShareTree | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if ttl_seconds > 0 and (time.monotonic() - entry.created_at) > ttl_seconds:
            self._entries.pop(key, None)
            return None
        return entry.tree

    def put(self, key: str, tree: ShareTree) -> None:
        self._entries[key] = _CacheEntry(tree=tree)

    def clear(self, key: str | None = None) -> None:
        if key is None:
            self._entries.clear()
        else:
            self._entries.pop(key, None)

    def __len__(self) -> int:
        return len(self._entries)


# --------------------------------------------------------------------------- HTTP 客户端


class ShareClient:
    """一个短生命周期的百度网盘分享客户端。

    调用方负责 ``await client.aclose()``，或使用 ``async with``；
    测试可传入 ``httpx.MockTransport``。
    """

    def __init__(
        self,
        cookies: str = "",
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        headers = {
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": PAN_MAIN,
        }
        if cookies:
            headers["Cookie"] = cookies
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(timeout),
            transport=transport,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ShareClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    # -- 基础请求 ---------------------------------------------------------

    async def _get_json(
        self,
        url: str,
        params: dict[str, Any],
        *,
        referer: str = PAN_MAIN,
        raw_errors: bool = False,
    ) -> dict[str, Any]:
        try:
            response = await self._client.get(url, params=params, headers={"Referer": referer})
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            if raw_errors:
                raise
            raise ShareParseError("百度网盘接口请求失败，请检查网络后重试。", detail=str(exc)) from exc
        except ValueError as exc:
            if raw_errors:
                raise
            raise ShareParseError("百度网盘接口返回了无法解析的数据。", detail=str(exc)) from exc
        if not isinstance(data, dict):
            raise ShareParseError("百度网盘接口返回格式异常。")
        return data

    async def _get_text(self, url: str) -> str:
        try:
            response = await self._client.get(url, headers={"Referer": PAN_MAIN})
            response.raise_for_status()
            return response.text
        except httpx.HTTPError as exc:
            raise ShareParseError("分享页面请求失败，请检查网络后重试。", detail=str(exc)) from exc

    @staticmethod
    def _errno(data: dict[str, Any]) -> int:
        try:
            return int(data.get("errno") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _error_text(data: dict[str, Any]) -> str:
        for key in ("show_msg", "err_msg", "error_msg", "message"):
            value = data.get(key)
            if value:
                return str(value)
        return ""

    # -- 解析分享 ---------------------------------------------------------

    async def resolve(self, surl: str, pwd: str = "") -> ShareInfo:
        """解析分享链接，必要时校验提取码，并获取 bdstoken。"""

        info: ShareInfo | None = None
        needs_pwd = False
        page_error: Exception | None = None
        try:
            info, needs_pwd = await self._fetch_share_info(surl)
        except (httpx.HTTPError, ValueError) as exc:
            page_error = exc

        if info is None:
            try:
                info = await self._resolve_via_page(surl)
            except ShareParseError:
                if page_error is not None:
                    raise ShareParseError("分享链接解析失败，请检查链接是否有效。", detail=str(page_error)) from page_error
                raise
            needs_pwd = needs_pwd or bool(pwd)

        bdstoken = await self._fetch_bdstoken()
        info.bdstoken = bdstoken
        info.pwd = pwd
        if needs_pwd:
            if not pwd:
                raise PasswordRequiredError()
            await self._verify(surl, info, pwd)
        return info

    async def _fetch_share_info(self, surl: str) -> tuple[ShareInfo | None, bool]:
        params = {
            "appid": "250528",
            "shorturl": shorturl_for_info(surl),
            "root": "1",
            "referer": PAN_MAIN,
        }
        data = await self._get_json(SHORTURL_API, params, raw_errors=True)
        errno = self._errno(data)
        share_id = str(data.get("shareid") or "").strip()
        uk = str(data.get("uk") or "").strip()
        if errno == -9:
            if not share_id or not uk:
                return None, True
            return (
                ShareInfo(
                    surl=surl,
                    share_id=share_id,
                    uk=uk,
                    title=str(data.get("title") or ""),
                ),
                True,
            )
        if errno != 0:
            message = self._error_text(data)
            if "提取码" in message or "密码" in message:
                return None, True
            if errno in {-3, -4, 404, 105}:
                raise ShareNotFoundError()
            raise ShareParseError(message or "分享链接解析失败，请检查链接是否有效。")
        if not share_id or not uk:
            return None, False
        return (
            ShareInfo(
                surl=surl,
                share_id=share_id,
                uk=uk,
                title=str(data.get("title") or ""),
            ),
            False,
        )

    async def _fetch_bdstoken(self) -> str:
        params = {
            "app_id": "250528",
            "web": "1",
            "clienttype": "0",
            "fields": '["bdstoken"]',
        }
        try:
            data = await self._get_json(TEMPLATE_API, params)
        except ShareParseError:
            return ""
        result = data.get("result")
        if isinstance(result, dict):
            return str(result.get("bdstoken") or "").strip()
        return str(data.get("bdstoken") or "").strip()

    async def _resolve_via_page(self, surl: str) -> ShareInfo:
        url = SHARE_PAGE.format(surl=shorturl_for_info(surl))
        last_error: Exception | None = None
        html = ""
        for attempt in range(2):
            try:
                html = await self._get_text(url)
                break
            except ShareParseError as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(1.0)
        if not html:
            raise ShareParseError("分享页面无响应，请稍后重试。", detail=str(last_error) if last_error else "")

        share_id = self._scrape(html, r"""["']shareid["']\s*:\s*["']?(\d+)""")
        uk = self._scrape(html, r"""["'](?:share_uk|uk)["']\s*:\s*["']?(\d+)""")
        if not share_id or not uk:
            raise ShareParseError("分享页面解析失败，链接可能已失效。")
        title = self._scrape(html, r"<title>([^<]{1,120})</title>") or ""
        return ShareInfo(surl=surl, share_id=share_id, uk=uk, title=title.strip())

    @staticmethod
    def _scrape(html: str, pattern: str) -> str:
        match = re.search(pattern, html)
        return match.group(1).strip() if match else ""

    async def _verify(self, surl: str, info: ShareInfo, pwd: str) -> None:
        params = {
            "shareid": info.share_id,
            "time": str(int(time.time() * 1000)),
            "clienttype": "1",
            "uk": info.uk,
        }
        form = {
            "pwd": pwd,
            "vcode": "null",
            "vcode_str": "null",
            "bdstoken": info.bdstoken,
        }
        referer = SHARE_PAGE.format(surl=shorturl_for_info(surl))
        try:
            response = await self._client.post(
                VERIFY_API,
                params=params,
                data=form,
                headers={"Referer": referer},
                timeout=20.0,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise ShareParseError("提取码校验请求失败，请稍后重试。", detail=str(exc)) from exc
        except ValueError as exc:
            raise ShareParseError("提取码校验返回格式异常。", detail=str(exc)) from exc
        if not isinstance(data, dict):
            raise ShareParseError("提取码校验返回格式异常。")
        errno = self._errno(data)
        if errno == -9:
            raise PasswordInvalidError()
        if errno not in {0}:
            raise ShareParseError(self._error_text(data) or "提取码校验失败。")

    # -- 转存 -------------------------------------------------------------

    async def transfer(self, info: ShareInfo, fs_ids: list[int | str], target_dir: str) -> dict[str, Any]:
        """把选中 fs_id 转存到 ``target_dir``。

        Returns:
            ``{"own_share": bool, "filenames": list[str], "errno": int}``；
            错误情况抛 ``TransferError``，自己的分享不抛错而是返回 ``own_share=True``。
        """

        normalized_ids = [str(item) for item in fs_ids if str(item).strip()]
        if not normalized_ids:
            raise TransferError("没有可转存的文件。")
        params = {
            "app_id": "250528",
            "channel": "chunlei",
            "clienttype": "0",
            "web": "1",
            "bdstoken": info.bdstoken,
            "shareid": info.share_id,
            "from": info.uk,
        }
        form = {
            "fsidlist": "[" + ",".join(normalized_ids) + "]",
            "path": target_dir,
        }
        referer = SHARE_PAGE.format(surl=shorturl_for_info(info.surl))
        try:
            response = await self._client.post(
                TRANSFER_API,
                params=params,
                data=form,
                headers={"Referer": referer},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise TransferError("转存请求失败，请检查网络后重试。", detail=str(exc)) from exc
        except ValueError as exc:
            raise TransferError("转存接口返回格式异常。", detail=str(exc)) from exc
        if not isinstance(data, dict):
            raise TransferError("转存接口返回格式异常。")

        errno = self._errno(data)
        if errno == 0:
            return {"own_share": False, "filenames": _extract_transfer_names(data), "errno": 0}
        if errno == 4:
            return {"own_share": False, "filenames": _extract_transfer_names(data), "errno": 4}
        if errno == 2:
            return {"own_share": True, "filenames": [], "errno": 2}
        if errno == 12:
            return self._handle_transfer_conflict(data)
        raise TransferError(self._error_text(data) or f"转存失败（errno={errno}）。")

    def _handle_transfer_conflict(self, data: dict[str, Any]) -> dict[str, Any]:
        infos = data.get("info")
        codes: set[int] = set()
        if isinstance(infos, list):
            for item in infos:
                if not isinstance(item, dict):
                    continue
                try:
                    codes.add(int(item.get("errno") or 0))
                except (TypeError, ValueError):
                    codes.add(0)
        if codes and codes <= {-30, 0}:
            return {"own_share": False, "filenames": _extract_transfer_names(data), "errno": 12}
        message = self._error_text(data)
        if -32 in codes or "空间不足" in message:
            raise TransferError(
                "网盘剩余空间不足，无法转存。请清理空间或改用单文件下载。",
                detail=message,
            )
        raise TransferError("转存失败：目标目录存在同名冲突。", detail=message)

    # -- 目录树 -----------------------------------------------------------

    async def _list_dir(self, info: ShareInfo, dir_path: str, *, limit: int) -> list[ShareNode]:
        params: dict[str, Any] = {
            "bdstoken": info.bdstoken,
            "web": "5",
            "app_id": "250528",
            "shorturl": shorturl_for_list(info.surl),
            "channel": "chunlei",
            "clienttype": "0",
            "root": "1" if not dir_path else "0",
            "num": str(max(1, min(limit, 100))),
        }
        if dir_path:
            params["dir"] = dir_path
        data = await self._get_json(LIST_API, params, referer=SHARE_PAGE.format(surl=shorturl_for_info(info.surl)))
        errno = self._errno(data)
        if errno == -9:
            raise PasswordRequiredError("分享需要提取码或提取码已失效，请重新发送链接和提取码。")
        if errno not in {0}:
            message = self._error_text(data)
            if "提取码" in message or "密码" in message:
                raise PasswordRequiredError()
            raise ShareParseError(message or "读取分享目录失败。")

        entries = data.get("list")
        if not isinstance(entries, list):
            return []
        nodes: list[ShareNode] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("server_filename") or entry.get("filename") or "").strip()
            if not name:
                continue
            is_dir = str(entry.get("isdir", "0")).strip() in {"1", "true", "True"} or entry.get("isdir") is True
            raw_path = str(entry.get("path") or "").strip()
            if not raw_path:
                raw_path = f"{dir_path}/{name}" if dir_path else f"/{name}"
            try:
                size = int(entry.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            nodes.append(
                ShareNode(
                    name=name,
                    path=raw_path,
                    is_dir=is_dir,
                    size=size,
                    fs_id=entry.get("fs_id"),
                )
            )
        return nodes

    async def build_tree(
        self,
        info: ShareInfo,
        *,
        max_depth: int = 3,
        max_items: int = 300,
        entries_per_dir: int = 60,
    ) -> ShareTree:
        """BFS 构建目录树，受深度/节点数/每目录条目数限制。"""

        tree = ShareTree(info=info, root_name=info.title or shorturl_for_info(info.surl))
        nodes: list[ShareNode] = []
        queue: list[tuple[str, int]] = [("", 0)]
        truncated = False

        while queue:
            dir_path, depth = queue.pop(0)
            if len(nodes) >= max_items:
                truncated = True
                break
            entries = await self._list_dir(info, dir_path, limit=entries_per_dir + 1)
            if len(entries) > entries_per_dir:
                entries = entries[:entries_per_dir]
                truncated = True
            for node in entries:
                if len(nodes) >= max_items:
                    truncated = True
                    break
                nodes.append(node)
                if node.is_dir:
                    if depth + 1 < max_depth:
                        queue.append((node.path, depth + 1))
                    else:
                        truncated = True

        tree.nodes = nodes
        tree.truncated = truncated
        return tree


# --------------------------------------------------------------------------- 文案


def _extract_transfer_names(data: dict[str, Any]) -> list[str]:
    """从转存响应中提取目标文件名（兼容不同字段）。"""

    extra = data.get("extra")
    items: Any = extra.get("list") if isinstance(extra, dict) else None
    if not isinstance(items, list):
        items = data.get("list")
    names: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                name = item.get("to") or item.get("server_filename") or item.get("from") or item.get("name")
                if name:
                    names.append(str(name))
            elif isinstance(item, str) and item.strip():
                names.append(item.strip())
    return names


def render_tree(tree: ShareTree, *, max_entries: int = 60, title: str = "") -> str:
    """把目录树渲染成聊天文本。"""

    lines: list[str] = [title or f"📁 {tree.root_name}"]
    visible = tree.nodes[:max_entries]
    for node in visible:
        depth = node.path.strip("/").count("/")
        indent = "  " * depth
        if node.is_dir:
            lines.append(f"{indent}📁 {node.name}/")
        else:
            lines.append(f"{indent}📄 {node.name}  ({format_size(node.size)})")

    hidden = len(tree.nodes) - len(visible)
    if hidden > 0:
        lines.append(f"… 其余 {hidden} 项已省略")
    if tree.truncated:
        lines.append("… 目录树已达到深度或数量上限，仅展示部分内容")

    file_count = len(tree.files())
    lines.append(f"\n共 {len(tree.nodes)} 个节点，{file_count} 个文件，合计 {format_size(tree.total_bytes())}")
    lines.append("发送 /pan file <路径> 或 /pan dir <路径> 下载；/pan look 重新查看本页。")
    return "\n".join(lines)


__all__ = [
    "BROWSER_UA",
    "LIST_API",
    "PAN_MAIN",
    "SHARE_PAGE",
    "SHORTURL_API",
    "ShareClient",
    "ShareSessionCache",
    "TEMPLATE_API",
    "VERIFY_API",
    "parse_share_input",
    "render_tree",
    "shorturl_for_info",
    "shorturl_for_list",
]
