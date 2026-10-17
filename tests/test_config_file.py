"""配置文件的生成与 TOML 序列化。"""

from __future__ import annotations

import tomllib
from pathlib import Path

from baidu_pan.config_file import dump_config_toml, ensure_config_file
from config import BaiduPanConfig


def test_dump_roundtrip() -> None:
    data = BaiduPanConfig().model_dump(mode="json")
    text = dump_config_toml(data)
    parsed = tomllib.loads(text)
    assert parsed == data
    assert "百度网盘下载助手配置" in text


def test_ensure_config_file_creates_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    data = BaiduPanConfig().model_dump(mode="json")

    assert ensure_config_file(path, data) is False
    assert path.exists()
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["plugin"]["config_version"] == "2.0.0"
    assert parsed["sending"]["base64_threshold_mb"] == 8

    assert ensure_config_file(path, data) is True


def test_ensure_config_file_does_not_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[plugin]\nenabled = false\n", encoding="utf-8")
    data = BaiduPanConfig().model_dump(mode="json")

    assert ensure_config_file(path, data) is True
    assert "enabled = false" in path.read_text(encoding="utf-8")
