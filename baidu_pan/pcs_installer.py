"""BaiduPCS-Go 安装器：按镜像顺序下载、校验大小与 SHA256、解压安装。"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from .errors import InstallError

GITHUB_RELEASE_URL = "https://github.com/qjfoidnh/BaiduPCS-Go/releases/download/{version}/{asset}"

# 内置 v4.0.2 官方 release 的大小与 SHA256（来自 GitHub Release API 的 digest 字段）。
_ASSETS: dict[tuple[str, str], tuple[str, int, str]] = {
    ("v4.0.2", "windows-x64"): (
        "BaiduPCS-Go-v4.0.2-windows-x64.zip",
        5711812,
        "ce72b3155a710b7c4a2b15611c3aebd11a057d7cccf0529e7703bdde04f0aa30",
    ),
    ("v4.0.2", "windows-arm64"): (
        "BaiduPCS-Go-v4.0.2-windows-arm64.zip",
        5067910,
        "576f7a7ab6df05996089edf766793c6bd083f19dc109fd9cf2d29b70c943191d",
    ),
    ("v4.0.2", "linux-amd64"): (
        "BaiduPCS-Go-v4.0.2-linux-amd64.zip",
        5527431,
        "b5f51388b510433668ca22fa5a1cb8840fb4d9725c5edc830554645010a62339",
    ),
    ("v4.0.2", "linux-arm64"): (
        "BaiduPCS-Go-v4.0.2-linux-arm64.zip",
        4959481,
        "d3568749f6475f9409b2d395466fa495133ca97f0582dbb15649a7074ed0b7fc",
    ),
    ("v4.0.2", "darwin-osx-amd64"): (
        "BaiduPCS-Go-v4.0.2-darwin-osx-amd64.zip",
        5625395,
        "0f002388aa944f18eeb0e2285f35463429070c78459c32b34210d59fd17be58c",
    ),
    ("v4.0.2", "darwin-osx-arm64"): (
        "BaiduPCS-Go-v4.0.2-darwin-osx-arm64.zip",
        5159924,
        "1d9b6f9f05c264a1345c198d12d612837838cea13640d3a10d1bed077068fef6",
    ),
}


def detect_platform() -> str:
    """返回当前平台的 asset key，例如 ``windows-x64``。"""

    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    if system == "windows":
        return f"windows-{'arm64' if arch == 'arm64' else 'x64'}"
    if system == "linux":
        return f"linux-{arch}"
    if system == "darwin":
        return f"darwin-osx-{arch}"
    raise InstallError(f"暂不支持当前平台：{platform.system()} {platform.machine()}")


def binary_name() -> str:
    return "BaiduPCS-Go.exe" if sys.platform.startswith("win") else "BaiduPCS-Go"


@dataclass(frozen=True)
class AssetInfo:
    """一个可安装 release asset 的元数据。"""

    version: str
    platform_key: str
    asset_name: str
    size: int | None
    sha256: str | None

    @property
    def github_url(self) -> str:
        return GITHUB_RELEASE_URL.format(version=self.version, asset=self.asset_name)


def asset_info(version: str, platform_key: str | None = None) -> AssetInfo:
    version = version.strip() or "v4.0.2"
    key = platform_key or detect_platform()
    known = _ASSETS.get((version, key))
    if known is not None:
        asset_name, size, sha256 = known
        return AssetInfo(version=version, platform_key=key, asset_name=asset_name, size=size, sha256=sha256)
    # 未知版本/平台：仍允许按 GitHub 命名规则下载，但无法校验摘要。
    return AssetInfo(version=version, platform_key=key, asset_name=f"BaiduPCS-Go-{version}-{key}.zip", size=None, sha256=None)


class PcsInstaller:
    """负责下载并安装 BaiduPCS-Go。"""

    def __init__(
        self,
        target_dir: Path,
        *,
        prefixes: list[str] | None = None,
        verify_checksum: bool = True,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._target_dir = target_dir
        self._prefixes = [prefix for prefix in (prefixes or []) if prefix]
        self._verify = verify_checksum
        self._timeout = timeout
        self._transport = transport
        self._logger = logger or logging.getLogger(__name__)

    @property
    def executable(self) -> Path:
        return self._target_dir / binary_name()

    def is_installed(self) -> bool:
        return self.executable.is_file() and self.executable.stat().st_size > 0

    def candidate_urls(self, info: AssetInfo) -> list[str]:
        direct = info.github_url
        return [f"{prefix}{direct}" for prefix in self._prefixes] + [direct]

    async def ensure(self, version: str, *, force: bool = False) -> Path:
        """确保可执行文件存在；已存在时直接复用。"""

        if self.is_installed() and not force:
            return self.executable
        return await self.install(version, force=force)

    async def install(self, version: str, *, force: bool = False) -> Path:
        info = asset_info(version)
        if self._verify and info.sha256 is None:
            raise InstallError(
                "当前版本/平台没有内置校验信息，已拒绝自动安装。"
                "请手动放置 BaiduPCS-Go 并在 download.baidu_pcs_go_path 中指定，"
                "或关闭 download.pcs_verify_checksum。"
            )

        self._target_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            follow_redirects=True,
            transport=self._transport,
            headers={"User-Agent": "MaiBot-BaiduPan/2.0"},
        ) as client:
            for url in self.candidate_urls(info):
                try:
                    payload = await self._download(client, url, info)
                except InstallError as exc:
                    errors.append(f"{url}: {exc}")
                    self._logger.warning("BaiduPCS-Go 下载源失败：%s（%s）", url, exc)
                    continue
                executable = self._extract(payload, info)
                if force:
                    self._logger.info("已重新安装 BaiduPCS-Go %s：%s", version, executable)
                else:
                    self._logger.info("已安装 BaiduPCS-Go %s：%s", version, executable)
                return executable

        detail = "；".join(errors[-3:]) if errors else "没有可用的下载源"
        raise InstallError("BaiduPCS-Go 下载失败，请检查网络或更换镜像源。", detail=detail)

    async def _download(self, client: httpx.AsyncClient, url: str, info: AssetInfo) -> bytes:
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                limit = (info.size or 64 * 1024 * 1024) + 1024 * 1024
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise InstallError("下载内容超过预期大小，已中止")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise InstallError("下载请求失败", detail=str(exc)) from exc

        payload = b"".join(chunks)
        if not payload:
            raise InstallError("下载内容为空")
        if not payload.startswith(b"PK\x03\x04"):
            raise InstallError("下载内容不是有效的 ZIP 文件（镜像可能返回了错误页）")
        if info.size is not None and len(payload) != info.size:
            raise InstallError(f"文件大小不符：期望 {info.size}，实际 {len(payload)}")
        if info.sha256 is not None and self._verify:
            digest = hashlib.sha256(payload).hexdigest()
            if digest != info.sha256:
                raise InstallError("SHA256 校验失败，已拒绝安装")
        return payload

    def _extract(self, payload: bytes, info: AssetInfo) -> Path:
        target = self.executable
        descriptor, temp_name = tempfile.mkstemp(suffix=".zip", dir=str(self._target_dir))
        os.close(descriptor)
        temp_zip = Path(temp_name)
        try:
            temp_zip.write_bytes(payload)
            with zipfile.ZipFile(temp_zip) as archive:
                member = next(
                    (name for name in archive.namelist() if Path(name).name == binary_name()),
                    None,
                )
                if member is None:
                    raise InstallError("ZIP 中未找到 BaiduPCS-Go 可执行文件")
                with archive.open(member) as source, tempfile.NamedTemporaryFile(
                    delete=False, dir=str(self._target_dir), suffix=".tmp"
                ) as destination:
                    shutil.copyfileobj(source, destination)
                    temp_path = Path(destination.name)
        except zipfile.BadZipFile as exc:
            raise InstallError("下载的 ZIP 文件已损坏", detail=str(exc)) from exc
        finally:
            temp_zip.unlink(missing_ok=True)

        try:
            if target.exists():
                target.unlink()
            os.replace(temp_path, target)
            if not sys.platform.startswith("win"):
                target.chmod(0o755)
        finally:
            temp_path.unlink(missing_ok=True)
        if not target.is_file() or target.stat().st_size == 0:
            raise InstallError("安装后的可执行文件为空")
        return target


__all__ = [
    "AssetInfo",
    "GITHUB_RELEASE_URL",
    "PcsInstaller",
    "asset_info",
    "binary_name",
    "detect_platform",
]
