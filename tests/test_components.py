"""组件注册契约：确保 Host 能收集到命令与全部 LLM 工具。"""

from __future__ import annotations

import inspect

import plugin as plugin_module


def registered_components() -> list[tuple[str, object]]:
    instance = plugin_module.create_plugin()
    components: list[tuple[str, object]] = []
    for name, method in inspect.getmembers(type(instance), predicate=inspect.isfunction):
        info = getattr(method, "__maibot_component_info__", None)
        if info is not None:
            components.append((name, info))
    return components


def test_command_registration() -> None:
    commands = {info.name: info for name, info in registered_components() if type(info).__name__ == "CommandComponentInfo"}
    assert set(commands) == {"pan"}
    assert commands["pan"].command_pattern.startswith("^/pan")


def test_tool_registration() -> None:
    tools = {info.name: info for name, info in registered_components() if type(info).__name__ == "ToolComponentInfo"}
    assert set(tools) == {"pan_list", "pan_transfer", "pan_download_dir", "pan_download_all"}
    for info in tools.values():
        assert info.description
        assert info.detailed_description


def test_tool_parameters() -> None:
    tools = {info.name: info for _name, info in registered_components() if type(info).__name__ == "ToolComponentInfo"}
    assert [parameter.name for parameter in tools["pan_list"].parameters] == ["link", "pwd"]
    assert [parameter.name for parameter in tools["pan_transfer"].parameters] == ["link", "pwd", "path"]
    assert [parameter.name for parameter in tools["pan_download_dir"].parameters] == ["folder_path", "link", "pwd"]
    assert [parameter.name for parameter in tools["pan_download_all"].parameters] == ["link", "pwd"]
    download_dir_params = tools["pan_download_dir"].parameters
    assert download_dir_params[0].required is True
