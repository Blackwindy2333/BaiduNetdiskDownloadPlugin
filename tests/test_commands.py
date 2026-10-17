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
