"""配置文件检查与默认配置生成。

Runner 通常会在首次加载时自动生成 ``config.toml``；本模块作为兜底：若插件
启动时仍找不到配置文件，则按配置模型的默认值原子写入一份，保证「无配置时
以默认设置创建」这一部署要求。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_HEADER = (
    "# 百度网盘下载助手配置（首次启动自动生成，可直接修改）",
    "# 修改保存后 Runner 会触发 on_config_update 热更新；敏感字段请勿提交到 git。",
    "",
)


def _format_toml_value(value: Any) -> str:
    """把配置标量/列表格式化为 TOML 值。"""

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_toml_value(item) for item in value) + "]"
    raise TypeError(f"不支持的配置值类型：{type(value).__name__}")


def dump_config_toml(config: Mapping[str, Any]) -> str:
    """把分节配置字典序列化为 TOML 文本（仅支持标量与数组）。"""

    lines: list[str] = list(_HEADER)
    for section, values in config.items():
        if not isinstance(values, Mapping):
            continue
        lines.append(f"[{section}]")
        for key, value in values.items():
            if value is None:
                continue
            lines.append(f"{key} = {_format_toml_value(value)}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def ensure_config_file(path: Path | str, default_config: Mapping[str, Any]) -> bool:
    """确保配置文件存在。

    Returns:
        ``True`` 表示文件原本就存在；``False`` 表示本次按默认值创建。
    """

    config_path = Path(path)
    if config_path.exists():
        return True
    _atomic_write_text(config_path, dump_config_toml(default_config))
    return False


__all__ = ["dump_config_toml", "ensure_config_file"]
