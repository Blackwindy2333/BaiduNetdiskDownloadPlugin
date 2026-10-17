"""生命周期：加载、卸载、配置热更新与兜底配置文件。"""

from __future__ import annotations

from conftest import apply_config


async def test_on_load_creates_config_and_log(plugin_env) -> None:
    plugin = plugin_env.plugin
    await plugin.on_load()

    config_path = plugin_env.plugin_dir / "config.toml"
    log_path = plugin_env.tmp_path / "data" / "logs" / "baidu-pan.log"
    assert config_path.exists()
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "已加载" in content
    assert "配置文件" in content


async def test_on_unload_is_idempotent(plugin_env) -> None:
    plugin = plugin_env.plugin
    await plugin.on_load()
    await plugin.on_unload()
    await plugin.on_unload()
    assert plugin._unloaded is True
    assert plugin._file_handler is None


async def test_existing_config_is_reused(plugin_env) -> None:
    config_path = plugin_env.plugin_dir / "config.toml"
    config_path.write_text("# 已存在的配置\n[plugin]\nenabled = true\n", encoding="utf-8")
    plugin = plugin_env.plugin
    await plugin.on_load()
    assert "# 已存在的配置" in config_path.read_text(encoding="utf-8")


async def test_on_config_update_ignores_other_scopes(plugin_env) -> None:
    plugin = plugin_env.plugin
    await plugin.on_load()
    await plugin.on_config_update("bot", {"plugin": {"enabled": True}}, "2.0.0")
    await plugin.on_config_update("self", {"plugin": {"enabled": True}}, "2.0.0")
    assert plugin._file_handler is not None


async def test_file_logging_can_be_disabled(plugin_env) -> None:
    apply_config(plugin_env.plugin, logging={"file_enabled": False})
    plugin = plugin_env.plugin
    await plugin.on_load()
    log_path = plugin_env.tmp_path / "data" / "logs" / "baidu-pan.log"
    assert not log_path.exists()
    assert plugin._file_handler is None


def test_create_plugin_factory() -> None:
    import plugin as plugin_module

    instance = plugin_module.create_plugin()
    assert isinstance(instance, plugin_module.BaiduPanPlugin)
    assert instance is not plugin_module.create_plugin()
