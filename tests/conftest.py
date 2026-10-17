"""测试公共夹具：路径注入、假上下文与配置助手。"""

from __future__ import annotations

import copy
import logging
import sys
from pathlib import Path
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeSend:
    """记录发送调用的假 send 能力，供命令/工具测试使用。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.custom_result: Any = {"success": True, "sent": True, "message_id": "fake-1"}

    async def text(self, content: str, stream_id: str = "", **kwargs: Any) -> Any:
        self.calls.append(("text", (content, stream_id), kwargs))
        return True

    async def image(self, image_data: str, stream_id: str = "", **kwargs: Any) -> Any:
        self.calls.append(("image", (image_data, stream_id), kwargs))
        return True

    async def custom(self, custom_type: str, data: Any, stream_id: str = "", **kwargs: Any) -> Any:
        self.calls.append(("custom", (custom_type, data, stream_id), kwargs))
        return self.custom_result


class FakeLogger:
    """轻量记录器，避免测试依赖真实日志输出。"""

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _record(self, level: str) -> Any:
        def handler(message: str, *args: Any, **kwargs: Any) -> None:
            self.records.append((level, message % args if args else message))

        return handler

    def __getattr__(self, name: str) -> Any:
        if name in {"debug", "info", "warning", "error", "critical", "exception"}:
            return self._record(name)
        raise AttributeError(name)


@pytest.fixture()
def plugin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """构造已注入假上下文与默认配置的插件实例，测试结束后清理文件日志。"""

    import plugin as plugin_module
    from baidu_pan.logging_utils import detach_file_handler
    from plugin import BaiduPanPlugin

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    monkeypatch.setattr(plugin_module, "_PLUGIN_DIR", plugin_dir)

    logger = logging.getLogger(f"test.baidu-pan.{id(tmp_path)}")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)

    send = FakeSend()
    ctx = SimpleNamespace(
        logger=logger,
        paths=SimpleNamespace(data_dir=tmp_path / "data", runtime_dir=tmp_path / "temp"),
        send=send,
    )
    instance = BaiduPanPlugin()
    instance._set_context(ctx)
    instance.set_plugin_config(instance.build_default_config())
    try:
        yield SimpleNamespace(
            plugin=instance,
            plugin_dir=plugin_dir,
            ctx=ctx,
            send=send,
            tmp_path=tmp_path,
            logger=logger,
        )
    finally:
        detach_file_handler(logger)
        logger.handlers.clear()


def apply_config(instance: Any, **sections: Any) -> None:
    """在默认配置基础上覆盖指定分节/字段并注入插件。"""

    data = copy.deepcopy(instance.build_default_config())
    for section, values in sections.items():
        if isinstance(values, dict):
            data.setdefault(section, {}).update(values)
        else:
            data[section] = values
    instance.set_plugin_config(data)


@pytest.fixture()
def make_plugin_config():
    """返回配置工厂，便于纯配置测试。"""

    from config import BaiduPanConfig

    def factory(**sections: Any) -> BaiduPanConfig:
        data: dict[str, Any] = {}
        for section, values in sections.items():
            data[section] = values
        return BaiduPanConfig(**data)

    return factory
