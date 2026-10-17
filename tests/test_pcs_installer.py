"""BaiduPCS-Go 安装器：平台识别、镜像回退、校验与解压。"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import httpx
import pytest

import baidu_pan.pcs_installer as installer_module
from baidu_pan.errors import InstallError
from baidu_pan.pcs_installer import (
    PcsInstaller,
    asset_info,
    binary_name,
    detect_platform,
)


def build_zip(entry: str | None = None, payload: bytes = b"fake-executable") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(entry or binary_name(), payload)
    return buffer.getvalue()


def test_detect_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(installer_module.platform, "machine", lambda: "AMD64")
    assert detect_platform() == "windows-x64"

    monkeypatch.setattr(installer_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(installer_module.platform, "machine", lambda: "aarch64")
    assert detect_platform() == "linux-arm64"

    monkeypatch.setattr(installer_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(installer_module.platform, "machine", lambda: "arm64")
    assert detect_platform() == "darwin-osx-arm64"

    monkeypatch.setattr(installer_module.platform, "system", lambda: "Plan9")
    with pytest.raises(InstallError):
        detect_platform()


def test_asset_info_known_and_unknown() -> None:
    known = asset_info("v4.0.2", "windows-x64")
    assert known.asset_name == "BaiduPCS-Go-v4.0.2-windows-x64.zip"
    assert known.size == 5711812
    assert known.sha256 == "ce72b3155a710b7c4a2b15611c3aebd11a057d7cccf0529e7703bdde04f0aa30"
    assert known.github_url.startswith("https://github.com/qjfoidnh/BaiduPCS-Go/releases/download/v4.0.2/")

    unknown = asset_info("v9.9.9", "windows-x64")
    assert unknown.sha256 is None


def test_candidate_urls_prefix_order(tmp_path: Path) -> None:
    installer = PcsInstaller(tmp_path, prefixes=["https://gh-proxy.com/", "https://git.yylx.win/"])
    info = asset_info("v4.0.2", "windows-x64")
    urls = installer.candidate_urls(info)
    assert urls[0].startswith("https://gh-proxy.com/https://github.com/")
    assert urls[1].startswith("https://git.yylx.win/https://github.com/")
    assert urls[-1].startswith("https://github.com/")


async def test_ensure_returns_existing_binary(tmp_path: Path) -> None:
    target = tmp_path / binary_name()
    target.write_bytes(b"existing")
    installer = PcsInstaller(tmp_path)
    assert await installer.ensure("v4.0.2") == target


async def test_install_verifies_and_writes_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = build_zip()
    monkeypatch.setattr(installer_module, "detect_platform", lambda: "test-platform")
    monkeypatch.setitem(
        installer_module._ASSETS,
        ("v9.9.9", "test-platform"),
        ("BaiduPCS-Go-v9.9.9-test-platform.zip", len(payload), hashlib.sha256(payload).hexdigest()),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    installer = PcsInstaller(
        tmp_path,
        prefixes=["https://mirror.example/"],
        transport=httpx.MockTransport(handler),
    )
    result = await installer.install("v9.9.9")
    assert result == tmp_path / binary_name()
    assert result.read_bytes() == b"fake-executable"


async def test_install_falls_back_to_next_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = build_zip()
    monkeypatch.setattr(installer_module, "detect_platform", lambda: "test-platform")
    monkeypatch.setitem(
        installer_module._ASSETS,
        ("v9.9.9", "test-platform"),
        ("asset.zip", len(payload), hashlib.sha256(payload).hexdigest()),
    )
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if str(request.url).startswith("https://broken.example/"):
            return httpx.Response(500, text="mirror down")
        return httpx.Response(200, content=payload)

    installer = PcsInstaller(
        tmp_path,
        prefixes=["https://broken.example/"],
        transport=httpx.MockTransport(handler),
    )
    await installer.install("v9.9.9")
    assert any(url.startswith("https://broken.example/") for url in seen)
    assert any(url.startswith("https://github.com/") for url in seen)


async def test_install_rejects_bad_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = build_zip()
    monkeypatch.setattr(installer_module, "detect_platform", lambda: "test-platform")
    monkeypatch.setitem(
        installer_module._ASSETS,
        ("v9.9.9", "test-platform"),
        ("asset.zip", len(payload), "0" * 64),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    installer = PcsInstaller(
        tmp_path, prefixes=[], transport=httpx.MockTransport(handler)
    )
    with pytest.raises(InstallError):
        await installer.install("v9.9.9")


async def test_install_rejects_non_zip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"<html>not a zip</html>"
    monkeypatch.setattr(installer_module, "detect_platform", lambda: "test-platform")
    monkeypatch.setitem(
        installer_module._ASSETS,
        ("v9.9.9", "test-platform"),
        ("asset.zip", len(payload), hashlib.sha256(payload).hexdigest()),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    installer = PcsInstaller(tmp_path, transport=httpx.MockTransport(handler))
    with pytest.raises(InstallError):
        await installer.install("v9.9.9")


async def test_unknown_version_requires_manual_install(tmp_path: Path) -> None:
    installer = PcsInstaller(tmp_path, verify_checksum=True)
    with pytest.raises(InstallError):
        await installer.install("v0.0.1")
