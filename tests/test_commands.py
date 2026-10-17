"""``/pan`` 命令入口的基础行为。"""

from __future__ import annotations

import asyncio

from conftest import apply_config
from baidu_pan.errors import AuthError
from baidu_pan.models import ShareInfo, ShareNode, ShareTree
from baidu_pan.pcs_client import AccountInfo, PcsResult
from baidu_pan.texts import PLUGIN_DISABLED_TEXT, UNKNOWN_COMMAND_TEXT


async def test_help_command(plugin_env) -> None:
    success, text, weight = await plugin_env.plugin.handle_pan(matched_groups={"args": "help"})
    assert success is True
    assert "百度网盘下载助手" in text
    assert "/pan qrlogin" in text
    assert "/pan add" in text
    assert weight == 2


async def test_no_args_shows_help(plugin_env) -> None:
    success, text, weight = await plugin_env.plugin.handle_pan(matched_groups={})
    assert success is True
    assert "/pan file" in text
    assert weight == 2


async def test_unknown_subcommand(plugin_env) -> None:
    success, text, weight = await plugin_env.plugin.handle_pan(
        matched_groups={"args": "frobnicate"}
    )
    assert success is True
    assert text == UNKNOWN_COMMAND_TEXT
    assert weight == 2


async def test_disabled_plugin(plugin_env) -> None:
    apply_config(plugin_env.plugin, plugin={"enabled": False})
    success, text, weight = await plugin_env.plugin.handle_pan(matched_groups={"args": "help"})
    assert success is True
    assert text == PLUGIN_DISABLED_TEXT
    assert weight == 0


async def test_help_alias_chinese(plugin_env) -> None:
    success, text, _ = await plugin_env.plugin.handle_pan(matched_groups={"args": "帮助"})
    assert success is True
    assert "/pan look" in text


def _admin_kwargs(user_id: str = "100", args: str = "") -> dict:
    return {
        "platform": "qq",
        "user_id": user_id,
        "matched_groups": {"args": args},
    }


async def test_add_whitelist_as_admin(plugin_env) -> None:
    apply_config(plugin_env.plugin, access={"admin_users": "qq:100"})
    plugin = plugin_env.plugin

    success, text, weight = await plugin.handle_pan(**_admin_kwargs(args="add 200"))
    assert success is True
    assert "已加入白名单" in text
    assert text.endswith("（当前共 1 人）")
    assert weight == 2

    success, text, _ = await plugin.handle_pan(**_admin_kwargs(args="wlist"))
    assert "qq:200" in text

    success, text, _ = await plugin.handle_pan(**_admin_kwargs(args="add 200"))
    assert "已在白名单中" in text

    success, text, _ = await plugin.handle_pan(**_admin_kwargs(args="del 200"))
    assert "已从白名单移除" in text

    success, text, _ = await plugin.handle_pan(**_admin_kwargs(args="del 200"))
    assert "白名单中没有该用户" in text


async def test_add_whitelist_via_at_segment(plugin_env) -> None:
    apply_config(plugin_env.plugin, access={"admin_users": "qq:100"})
    kwargs = _admin_kwargs(args="add")
    kwargs["message"] = {"message_segments": [{"type": "at", "data": {"qq": "246810"}}]}
    success, text, _ = await plugin_env.plugin.handle_pan(**kwargs)
    assert success is True
    assert "qq:246810" in text


async def test_non_admin_cannot_manage_whitelist(plugin_env) -> None:
    apply_config(plugin_env.plugin, access={"admin_users": "qq:100"})
    success, text, _ = await plugin_env.plugin.handle_pan(**_admin_kwargs(user_id="200", args="add 300"))
    assert success is True
    assert "仅管理员" in text


async def test_no_admin_configured(plugin_env) -> None:
    apply_config(plugin_env.plugin, access={"admin_users": ""})
    success, text, _ = await plugin_env.plugin.handle_pan(**_admin_kwargs(args="add 200"))
    assert success is True
    assert "尚未配置管理员" in text


async def test_invalid_target(plugin_env) -> None:
    apply_config(plugin_env.plugin, access={"admin_users": "qq:100"})
    success, text, _ = await plugin_env.plugin.handle_pan(**_admin_kwargs(args="add"))
    assert success is True
    assert "请提供有效的 QQ 号" in text


async def test_blacklist_user_is_silent(plugin_env) -> None:
    apply_config(plugin_env.plugin, access={"admin_users": "qq:100", "blacklist": "qq:100"})
    success, text, weight = await plugin_env.plugin.handle_pan(**_admin_kwargs(args="help"))
    assert success is True
    assert text == ""
    assert weight == 0


async def test_status_command(plugin_env) -> None:
    apply_config(plugin_env.plugin, access={"admin_users": "qq:100"})
    success, text, _ = await plugin_env.plugin.handle_pan(**_admin_kwargs(args="status"))
    assert success is True
    assert "管理员：1 人" in text
    assert "白名单：0 人" in text


class FakeShareClient:
    """记录调用并返回固定目录树的假分享客户端。"""

    instances: list["FakeShareClient"] = []

    def __init__(self, cookies: str, *, timeout: float, logger=None) -> None:
        self.cookies = cookies
        self.timeout = timeout
        self.closed = False
        self.resolved: tuple[str, str] | None = None
        FakeShareClient.instances.append(self)

    async def resolve(self, surl: str, pwd: str = "") -> ShareInfo:
        self.resolved = (surl, pwd)
        return ShareInfo(surl=surl, share_id="99", uk="88", pwd=pwd, bdstoken="tk", title="资料")

    async def build_tree(self, info: ShareInfo, **kwargs) -> ShareTree:
        self.tree_kwargs = kwargs
        nodes = [
            ShareNode(name="子目录", path="/子目录", is_dir=True, fs_id=1),
            ShareNode(name="说明.txt", path="/说明.txt", is_dir=False, size=2048, fs_id=2),
        ]
        return ShareTree(info=info, root_name="资料", nodes=nodes)

    async def transfer(self, info: ShareInfo, fs_ids, target_dir: str):
        self.transferred = (info.share_id, list(fs_ids), target_dir)
        return {"own_share": False, "filenames": ["说明.txt"], "errno": 0}

    async def aclose(self) -> None:
        self.closed = True


async def test_browse_share_and_look(plugin_env, monkeypatch) -> None:
    import plugin as plugin_module

    FakeShareClient.instances.clear()
    monkeypatch.setattr(plugin_module, "ShareClient", FakeShareClient)
    monkeypatch.setattr(plugin_module, "load_cookie_header", lambda path: "BDUSS=fake")

    args = "https://pan.baidu.com/s/1AbCdEf 提取码: 052x"
    success, text, weight = await plugin_env.plugin.handle_pan(
        **_admin_kwargs(args=args), stream_id="stream-1"
    )
    assert success is True
    assert weight == 2
    assert "📁 资料" in text
    assert "📁 子目录/" in text
    assert "📄 说明.txt  (2.00 KB)" in text

    client = FakeShareClient.instances[0]
    assert client.cookies == "BDUSS=fake"
    assert client.resolved == ("1AbCdEf", "052x")
    assert client.closed is True

    success, text, _ = await plugin_env.plugin.handle_pan(
        matched_groups={"args": "look"}, stream_id="stream-1"
    )
    assert success is True
    assert "📁 资料" in text

    success, text, _ = await plugin_env.plugin.handle_pan(
        matched_groups={"args": "look"}, stream_id="other-stream"
    )
    assert success is True
    assert "还没有缓存的分享" in text


async def test_browse_without_login_is_anonymous(plugin_env, monkeypatch) -> None:
    import plugin as plugin_module

    FakeShareClient.instances.clear()
    monkeypatch.setattr(plugin_module, "ShareClient", FakeShareClient)

    def _raise_auth(path: str) -> str:
        raise AuthError()

    monkeypatch.setattr(plugin_module, "load_cookie_header", _raise_auth)
    success, text, _ = await plugin_env.plugin.handle_pan(
        matched_groups={"args": "1AbCdEf"}, stream_id="stream-anon"
    )
    assert success is True
    assert "📁 资料" in text
    assert FakeShareClient.instances[0].cookies == ""


async def test_ls_refreshes_cached_share(plugin_env, monkeypatch) -> None:
    import plugin as plugin_module

    FakeShareClient.instances.clear()
    monkeypatch.setattr(plugin_module, "ShareClient", FakeShareClient)
    monkeypatch.setattr(plugin_module, "load_cookie_header", lambda path: "")

    success, _, _ = await plugin_env.plugin.handle_pan(
        matched_groups={"args": "1AbCdEf"}, stream_id="stream-ls"
    )
    assert success is True
    success, text, _ = await plugin_env.plugin.handle_pan(
        matched_groups={"args": "ls"}, stream_id="stream-ls"
    )
    assert success is True
    assert "📁 资料" in text
    assert FakeShareClient.instances[-1].resolved == ("1AbCdEf", "")


class FakePcsClient:
    """假 BaiduPCS-Go 客户端，记录命令并返回固定账号。"""

    calls: list[tuple[str, tuple]] = []

    def __init__(self, executable, *, config_dir=None, logger=None) -> None:
        self.executable = executable
        self.config_dir = config_dir

    async def login_bduss(self, bduss: str, stoken: str = "") -> AccountInfo:
        FakePcsClient.calls.append(("login_bduss", (bduss, stoken)))
        return AccountInfo(uid=100, name="测试账号")

    async def login_cookies(self, cookies: str) -> AccountInfo:
        FakePcsClient.calls.append(("login_cookies", (cookies,)))
        return AccountInfo(uid=100, name="测试账号")

    async def login_password(self, username: str, password: str) -> AccountInfo:
        FakePcsClient.calls.append(("login_password", (username, password)))
        return AccountInfo(uid=100, name="测试账号")

    async def logout(self) -> PcsResult:
        FakePcsClient.calls.append(("logout", ()))
        return PcsResult(code=0, stdout="退出登录成功\n")


async def test_install_command_uses_configured_path(plugin_env, monkeypatch) -> None:
    import plugin as plugin_module

    exe = plugin_env.tmp_path / "BaiduPCS-Go.exe"
    exe.write_bytes(b"stub")
    apply_config(
        plugin_env.plugin,
        access={"admin_users": "qq:100"},
        download={"baidu_pcs_go_path": str(exe)},
    )
    success, text, _ = await plugin_env.plugin.handle_pan(
        **_admin_kwargs(args="install")
    )
    assert success is True
    assert "已就绪" in text
    assert str(exe) in text

    # 非管理员不能安装
    success, text, _ = await plugin_env.plugin.handle_pan(
        **_admin_kwargs(user_id="200", args="install")
    )
    assert success is True
    assert "仅管理员" in text


async def test_login_commands(plugin_env, monkeypatch) -> None:
    import plugin as plugin_module

    FakePcsClient.calls.clear()
    monkeypatch.setattr(plugin_module, "PcsClient", FakePcsClient)
    apply_config(plugin_env.plugin, access={"admin_users": "qq:100"})
    plugin = plugin_env.plugin

    success, text, _ = await plugin.handle_pan(**_admin_kwargs(args="bduss abc123 stoken456"))
    assert success is True
    assert "登录成功" in text
    assert ("login_bduss", ("abc123", "stoken456")) in FakePcsClient.calls

    success, text, _ = await plugin.handle_pan(**_admin_kwargs(args="cookies BDUSS=x; STOKEN=y"))
    assert success is True
    assert any(call[0] == "login_cookies" for call in FakePcsClient.calls)

    success, text, _ = await plugin.handle_pan(**_admin_kwargs(args="login user pass"))
    assert success is True
    assert any(call[0] == "login_password" for call in FakePcsClient.calls)

    success, text, _ = await plugin.handle_pan(**_admin_kwargs(args="unlogin"))
    assert success is True
    assert "已退出" in text


async def test_login_command_requires_arguments(plugin_env) -> None:
    apply_config(plugin_env.plugin, access={"admin_users": "qq:100"})
    success, text, _ = await plugin_env.plugin.handle_pan(**_admin_kwargs(args="login onlyuser"))
    assert success is True
    assert "用法" in text
    success, text, _ = await plugin_env.plugin.handle_pan(**_admin_kwargs(args="cookies"))
    assert success is True
    assert "用法" in text


async def test_qrlogin_starts_background_task(plugin_env, monkeypatch) -> None:
    apply_config(plugin_env.plugin, access={"admin_users": "qq:100"})
    plugin = plugin_env.plugin
    ran: list[str] = []

    async def fake_flow(stream_id: str) -> None:
        ran.append(stream_id)

    monkeypatch.setattr(plugin, "_qr_login_flow", fake_flow)
    success, text, _ = await plugin.handle_pan(
        **_admin_kwargs(args="qrlogin"), stream_id="stream-qr"
    )
    assert success is True
    assert "正在准备扫码登录" in text
    task = plugin._qr_task
    assert task is not None
    await task
    assert ran == ["stream-qr"]


async def test_pan_transfer_tool(plugin_env, monkeypatch) -> None:
    import plugin as plugin_module

    FakeShareClient.instances.clear()
    monkeypatch.setattr(plugin_module, "ShareClient", FakeShareClient)
    monkeypatch.setattr(plugin_module, "load_cookie_header", lambda path: "")
    apply_config(plugin_env.plugin, access={"admin_users": "qq:100"})

    result = await plugin_env.plugin.tool_pan_transfer(
        link="https://pan.baidu.com/s/1AbCdEf",
        path="说明.txt",
        platform="qq",
        user_id="100",
        stream_id="stream-transfer",
    )
    assert "已转存" in result["content"]
    assert "/我的资源/AutoTransfer" in result["content"]
    client = FakeShareClient.instances[-1]
    assert client.transferred[1] == [2]


async def test_pan_transfer_requires_whitelist(plugin_env, monkeypatch) -> None:
    import plugin as plugin_module

    monkeypatch.setattr(plugin_module, "ShareClient", FakeShareClient)
    monkeypatch.setattr(plugin_module, "load_cookie_header", lambda path: "")
    apply_config(plugin_env.plugin, access={"admin_users": "qq:100"})

    result = await plugin_env.plugin.tool_pan_transfer(
        link="https://pan.baidu.com/s/1AbCdEf",
        path="说明.txt",
        platform="qq",
        user_id="200",
        stream_id="stream-denied",
    )
    assert "没有下载权限" in result["content"]


async def test_pan_transfer_without_link_uses_cache(plugin_env, monkeypatch) -> None:
    import plugin as plugin_module

    FakeShareClient.instances.clear()
    monkeypatch.setattr(plugin_module, "ShareClient", FakeShareClient)
    monkeypatch.setattr(plugin_module, "load_cookie_header", lambda path: "")
    apply_config(plugin_env.plugin, access={"admin_users": "qq:100"})
    plugin = plugin_env.plugin

    success, _, _ = await plugin.handle_pan(
        matched_groups={"args": "1AbCdEf"}, stream_id="stream-cache"
    )
    assert success is True
    result = await plugin.tool_pan_transfer(
        path="说明.txt",
        platform="qq",
        user_id="100",
        stream_id="stream-cache",
    )
    assert "已转存" in result["content"]
