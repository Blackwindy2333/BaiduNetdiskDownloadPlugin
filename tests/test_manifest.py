"""清单文件与仓库骨架的静态校验。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "_manifest.json"

ALLOWED_CAPABILITIES = {
    "send.text",
    "send.image",
    "send.emoji",
    "send.command",
    "send.forward",
    "send.hybrid",
    "send.custom",
    "config.get",
    "config.get_plugin",
    "config.get_all",
}


def test_manifest_is_valid_json() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(manifest, dict)


def test_manifest_identity_and_versions() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 2
    assert manifest["id"] == "blackbee.baidu-pan"
    assert manifest["id"] == manifest["id"].lower()
    assert manifest["version"] == "2.0.0"
    assert manifest["license"] == "GPL-3.0"
    assert manifest["author"]["name"] == "BlackBee114514"
    assert manifest["sdk"]["min_version"] == "2.14.0"
    assert manifest["host_application"]["min_version"] == "1.3.0"
    assert manifest["i18n"]["default_locale"] == "zh-CN"


def test_manifest_capabilities_declared() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    capabilities = manifest["capabilities"]
    assert capabilities == ["send.text", "send.image", "send.custom"]
    assert set(capabilities) <= ALLOWED_CAPABILITIES


def test_manifest_dependencies() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    dependencies = manifest["dependencies"]
    assert isinstance(dependencies, list) and dependencies
    httpx_dep = next(item for item in dependencies if item["name"] == "httpx")
    assert httpx_dep["type"] == "python_package"
    assert httpx_dep["version_spec"].startswith(">=")


def test_manifest_icon_and_urls() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["display"]["icon"]["type"] == "lucide"
    assert manifest["urls"]["repository"].startswith("https://github.com/")
    assert manifest["urls"]["issues"].startswith("https://github.com/")


def test_entrypoint_factory() -> None:
    source = (ROOT / "plugin.py").read_text(encoding="utf-8")
    assert "def create_plugin()" in source
    assert "class BaiduPanPlugin" in source


def test_gitignore_excludes_runtime_files() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("/config.toml", "/logs/", "/data/", "__pycache__/"):
        assert pattern in ignored


def test_license_present() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in license_text
