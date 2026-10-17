"""分享协议与目录树测试：全部走 httpx.MockTransport，不访问真实网络。"""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from baidu_pan.errors import (
    PasswordInvalidError,
    PasswordRequiredError,
    ShareParseError,
)
from baidu_pan.models import ShareInfo, ShareTree
from baidu_pan.share import (
    ShareClient,
    ShareSessionCache,
    parse_share_input,
    render_tree,
    shorturl_for_info,
    shorturl_for_list,
)


def make_client(routes: dict[str, Callable[[httpx.Request], httpx.Response]]):
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        route = routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, text="not found")
        return route(request)

    client = ShareClient("", transport=httpx.MockTransport(handler))
    return client, calls


def json_response(payload: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, json=payload)


# --------------------------------------------------------------------------- 链接解析


def test_parse_share_input_full_link_with_pwd() -> None:
    surl, pwd = parse_share_input("链接: https://pan.baidu.com/s/1AbCdEf 提取码: 052x")
    assert surl == "1AbCdEf"
    assert pwd == "052x"


def test_parse_share_input_bare_surl_and_explicit_pwd() -> None:
    surl, pwd = parse_share_input("1AbCdEf", password="abcd")
    assert surl == "1AbCdEf"
    assert pwd == "abcd"


def test_parse_share_input_rejects_plain_words() -> None:
    with pytest.raises(ShareParseError):
        parse_share_input("frobnicate")


def test_shorturl_helpers_handle_leading_one() -> None:
    assert shorturl_for_info("AbCdEf") == "1AbCdEf"
    assert shorturl_for_info("1AbCdEf") == "1AbCdEf"
    assert shorturl_for_list("1AbCdEf") == "AbCdEf"
    assert shorturl_for_list("AbCdEf") == "AbCdEf"


# --------------------------------------------------------------------------- 解析流程


async def test_resolve_without_password() -> None:
    client, calls = make_client(
        {
            "/api/shorturlinfo": json_response({"errno": 0, "shareid": 111, "uk": 222, "title": "资料"}),
            "/api/gettemplatevariable": json_response({"errno": 0, "result": {"bdstoken": "tk-1"}}),
        }
    )
    async with client:
        info = await client.resolve("1AbCdEf")
    assert info.share_id == "111"
    assert info.uk == "222"
    assert info.bdstoken == "tk-1"
    assert info.title == "资料"
    assert [call.url.path for call in calls] == ["/api/shorturlinfo", "/api/gettemplatevariable"]


async def test_resolve_verifies_password() -> None:
    client, calls = make_client(
        {
            "/api/shorturlinfo": json_response({"errno": -9, "shareid": 111, "uk": 222}),
            "/api/gettemplatevariable": json_response({"errno": 0, "result": {"bdstoken": "tk-1"}}),
            "/share/verify": json_response({"errno": 0}),
        }
    )
    async with client:
        info = await client.resolve("1AbCdEf", "052x")
    assert info.pwd == "052x"
    verify = next(call for call in calls if call.url.path == "/share/verify")
    assert b"pwd=052x" in verify.content
    assert verify.url.params["shareid"] == "111"


async def test_resolve_wrong_password_raises() -> None:
    client, _ = make_client(
        {
            "/api/shorturlinfo": json_response({"errno": -9, "shareid": 111, "uk": 222}),
            "/api/gettemplatevariable": json_response({"result": {"bdstoken": "tk"}}),
            "/share/verify": json_response({"errno": -9}),
        }
    )
    async with client:
        with pytest.raises(PasswordInvalidError):
            await client.resolve("1AbCdEf", "0000")


async def test_resolve_missing_password_raises() -> None:
    client, _ = make_client(
        {"/api/shorturlinfo": json_response({"errno": -9, "shareid": 111, "uk": 222})}
    )
    async with client:
        with pytest.raises(PasswordRequiredError):
            await client.resolve("1AbCdEf")


async def test_resolve_falls_back_to_share_page() -> None:
    page = (
        "<html><script>var ctx={\"shareid\":333,\"share_uk\":444,"
        "\"bdstoken\":\"page-token\"};</script><title>分享页</title></html>"
    )
    client, calls = make_client(
        {
            "/api/shorturlinfo": lambda request: httpx.Response(500, text="boom"),
            "/s/1AbCdEf": lambda request: httpx.Response(200, text=page),
            "/api/gettemplatevariable": json_response({"result": {"bdstoken": "tk-2"}}),
        }
    )
    async with client:
        info = await client.resolve("1AbCdEf")
    assert info.share_id == "333"
    assert info.uk == "444"
    assert info.bdstoken == "tk-2"
    assert any(call.url.path == "/s/1AbCdEf" for call in calls)


# --------------------------------------------------------------------------- 目录树


def _tree_routes() -> dict[str, Callable[[httpx.Request], httpx.Response]]:
    def list_route(request: httpx.Request) -> httpx.Response:
        directory = request.url.params.get("dir", "")
        if not directory:
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "list": [
                        {"server_filename": "子目录", "isdir": 1, "path": "/子目录", "size": 0, "fs_id": 1},
                        {"server_filename": "说明.txt", "isdir": 0, "path": "/说明.txt", "size": 2048, "fs_id": 2},
                    ],
                },
            )
        if directory == "/子目录":
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "list": [
                        {"server_filename": "深层.txt", "isdir": 0, "path": "/子目录/深层.txt", "size": 10, "fs_id": 3}
                    ],
                },
            )
        return httpx.Response(200, json={"errno": 0, "list": []})

    return {"/share/list": list_route}


async def test_build_tree_and_render() -> None:
    client, calls = make_client(_tree_routes())
    info = ShareInfo(surl="1AbCdEf", share_id="1", uk="2", bdstoken="tk", title="资料")
    async with client:
        tree = await client.build_tree(info, max_depth=2, max_items=10, entries_per_dir=10)
    assert [node.name for node in tree.nodes] == ["子目录", "说明.txt", "深层.txt"]
    assert tree.truncated is False
    assert tree.total_bytes() == 2058
    assert len(tree.files()) == 2
    assert any(call.url.params.get("dir") == "/子目录" for call in calls)
    text = render_tree(tree)
    assert "📁 资料" in text
    assert "📁 子目录/" in text
    assert "📄 说明.txt  (2.00 KB)" in text
    assert "📄 深层.txt" in text


async def test_build_tree_truncates_at_depth_limit() -> None:
    client, _ = make_client(_tree_routes())
    info = ShareInfo(surl="1AbCdEf", share_id="1", uk="2", bdstoken="tk")
    async with client:
        tree = await client.build_tree(info, max_depth=1, max_items=10, entries_per_dir=10)
    assert [node.name for node in tree.nodes] == ["子目录", "说明.txt"]
    assert tree.truncated is True


async def test_list_requires_password_error() -> None:
    client, _ = make_client({"/share/list": json_response({"errno": -9})})
    info = ShareInfo(surl="1AbCdEf", share_id="1", uk="2", bdstoken="tk")
    async with client:
        with pytest.raises(PasswordRequiredError):
            await client.build_tree(info)


# --------------------------------------------------------------------------- 缓存


def test_session_cache_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = ShareSessionCache()
    share_tree = ShareTree(info=ShareInfo(surl="1AbCdEf", share_id="1", uk="2"), root_name="资料")
    cache.put("s1", share_tree)
    assert cache.get("s1", 60) is share_tree
    assert cache.get("missing", 60) is None

    import baidu_pan.share as share_module

    base = share_module.time.monotonic()
    monkeypatch.setattr(share_module.time, "monotonic", lambda: base + 120)
    assert cache.get("s1", 60) is None
    assert len(cache) == 0
