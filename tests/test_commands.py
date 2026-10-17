"""``/pan`` 命令入口的基础行为。"""

from __future__ import annotations

from conftest import apply_config
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
