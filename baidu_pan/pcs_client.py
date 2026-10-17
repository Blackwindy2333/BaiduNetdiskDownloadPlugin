"""BaiduPCS-Go 子进程封装：统一环境变量、超时、输出解码与结果解析。"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .errors import PcsError, PcsTimeoutError

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_PROGRESS_RE = re.compile(r"↓\s*(\S+)/(\S+)")
_SPEED_RE = re.compile(r"↓\s*\S+/\S+\s+(\S+)/s")

_LIST_LINE_RE = re.compile(
    r"^\s*\d+\s+\d+\s+\d+\s+(\S+)\s+"
    r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?\s+"
    r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?\s+"
    r"(?:[0-9a-fA-F]{32}\s+)?(.+?)\s*$"
)
_WHO_UID_RE = re.compile(r"uid[:：]\s*(\d+)")
_WHO_NAME_RE = re.compile(r"用户名[:：]\s*([^,，\r\n]+)")


@dataclass
class PcsResult:
    """一次 BaiduPCS-Go 调用的结果。"""

    code: int
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0

    @property
    def output(self) -> str:
        return f"{self.stdout}\n{self.stderr}".strip()

    def contains_error_words(self) -> bool:
        return "失败" in self.output or "错误" in self.output


@dataclass
class AccountInfo:
    uid: int
    name: str
    raw: str = ""


@dataclass
class RemoteEntry:
    name: str
    is_dir: bool
    size: int = 0
    raw: str = ""


@dataclass
class DownloadOutcome:
    success: bool
    result: PcsResult
    failed_lines: list[str] = field(default_factory=list)


@dataclass
class DownloadProgress:
    """下载进度（来自 BaiduPCS-Go 的 \\r 进度行）。"""

    downloaded: int
    total: int
    percent: float
    speed: str = ""


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


class PcsClient:
    """对 BaiduPCS-Go 命令行的一层薄封装。"""

    def __init__(
        self,
        executable: Path,
        *,
        config_dir: Path | None = None,
        timeout: float = 30.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._executable = Path(executable)
        self._config_dir = Path(config_dir) if config_dir is not None else None
        self._default_timeout = timeout
        self._logger = logger or logging.getLogger(__name__)

    @property
    def executable(self) -> Path:
        return self._executable

    @property
    def available(self) -> bool:
        return self._executable.is_file()

    def _environment(self) -> dict[str, str]:
        env = dict(os.environ)
        if self._config_dir is not None:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            env["BAIDUPCS_GO_CONFIG_DIR"] = str(self._config_dir)
        return env

    async def run(
        self,
        args: list[str],
        *,
        timeout: float | None = None,
        input_data: str | None = None,
    ) -> PcsResult:
        """执行一次 BaiduPCS-Go 命令，返回解码后的输出。"""

        if not self.available:
            raise PcsError("BaiduPCS-Go 不存在，请先执行 /pan install 安装。")
        if not args:
            raise PcsError("拒绝执行空命令（BaiduPCS-Go 无参数会进入交互模式）。")

        started = time.monotonic()
        kwargs: dict[str, object] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "stdin": asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL,
            "env": self._environment(),
        }
        if _CREATE_NO_WINDOW:
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        try:
            process = await asyncio.create_subprocess_exec(str(self._executable), *args, **kwargs)
        except OSError as exc:
            raise PcsError("无法启动 BaiduPCS-Go，请检查文件权限。", detail=str(exc)) from exc

        payload = input_data.encode("utf-8") if input_data is not None else None
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload),
                timeout=timeout if timeout is not None else self._default_timeout,
            )
        except asyncio.CancelledError:
            process.kill()
            try:
                await asyncio.wait_for(process.communicate(), timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            raise
        except asyncio.TimeoutError as exc:
            process.kill()
            try:
                await asyncio.wait_for(process.communicate(), timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            raise PcsTimeoutError("BaiduPCS-Go 命令执行超时。") from exc

        result = PcsResult(
            code=process.returncode or 0,
            stdout=_decode(stdout),
            stderr=_decode(stderr),
            duration=time.monotonic() - started,
        )
        self._logger.debug(
            "BaiduPCS-Go %s 结束：code=%s，耗时 %.1fs",
            " ".join(args)[:120],
            result.code,
            result.duration,
        )
        return result

    async def version(self) -> str:
        result = await self.run(["--version"], timeout=10)
        match = re.search(r"v\d+\.\d+\.\d+(?:-[\w.]+)?", result.output)
        return match.group(0) if match else result.output.strip()

    async def who(self) -> AccountInfo | None:
        result = await self.run(["who"], timeout=15)
        uid_match = _WHO_UID_RE.search(result.output)
        if uid_match is None or int(uid_match.group(1)) <= 0:
            return None
        name_match = _WHO_NAME_RE.search(result.output)
        return AccountInfo(
            uid=int(uid_match.group(1)),
            name=name_match.group(1).strip() if name_match else "",
            raw=result.output,
        )

    async def login_bduss(self, bduss: str, stoken: str = "") -> AccountInfo | None:
        args = ["login", f"-bduss={bduss.strip()}"]
        if stoken.strip():
            args.append(f"-stoken={stoken.strip()}")
        result = await self.run(args, timeout=30)
        self._logger.info("BaiduPCS-Go BDUSS 登录输出：%s", _mask(result.output))
        if "登录成功" not in result.output:
            raise PcsError(_login_failure_text(result.output), detail=result.output)
        return await self.who()

    async def login_cookies(self, cookies: str) -> AccountInfo | None:
        result = await self.run(["login", f"-cookies={cookies.strip()}"], timeout=30)
        self._logger.info("BaiduPCS-Go Cookie 登录输出：%s", _mask(result.output))
        if "登录成功" not in result.output:
            raise PcsError(_login_failure_text(result.output), detail=result.output)
        return await self.who()

    async def login_password(self, username: str, password: str) -> AccountInfo | None:
        args = ["login", f"-username={username}", f"-password={password}"]
        result = await self.run(args, timeout=60)
        if "登录成功" not in result.output:
            # 旧版本可能不支持 -username/-password 参数，退回标准输入交互。
            self._logger.info("BaiduPCS-Go 参数登录未成功，尝试标准输入登录")
            result = await self.run(["login"], timeout=60, input_data=f"{username}\n{password}\n")
        if "登录成功" not in result.output:
            raise PcsError(_login_failure_text(result.output), detail=result.output)
        return await self.who()

    async def logout(self) -> PcsResult:
        return await self.run(["logout", "-y"], timeout=20)

    async def ls(self, directory: str) -> list[RemoteEntry]:
        result = await self.run(["ls", "-l", directory], timeout=30)
        if result.contains_error_words() and "目录" in result.output:
            raise PcsError("读取网盘目录失败，请确认路径存在且已登录。", detail=result.output)
        entries: list[RemoteEntry] = []
        for line in result.stdout.splitlines():
            match = _LIST_LINE_RE.match(line)
            if match is None:
                continue
            size_token, name = match.group(1), match.group(2).strip()
            is_dir = size_token == "-" or name.endswith("/")
            name = name.rstrip("/")
            if not name:
                continue
            try:
                size = 0 if is_dir else _parse_size(size_token)
            except ValueError:
                size = 0
            entries.append(RemoteEntry(name=name, is_dir=is_dir, size=size, raw=line))
        return entries

    async def download(
        self,
        paths: list[str],
        saveto: Path,
        *,
        overwrite: bool = True,
        timeout: float,
        on_progress: Callable[[DownloadProgress], None] | None = None,
    ) -> DownloadOutcome:
        saveto.mkdir(parents=True, exist_ok=True)
        args = ["download"]
        if overwrite:
            args.append("--ow")
        args.extend(["--saveto", str(saveto)])
        args.extend(paths)
        if on_progress is None:
            result = await self.run(args, timeout=timeout)
        else:
            result = await self._run_streaming(args, timeout=timeout, on_progress=on_progress)
        failed_lines = [
            line.strip()
            for line in result.stdout.splitlines()
            if "失败" in line and line.strip()
        ]
        success = (
            result.code == 0
            and "下载结束" in result.stdout
            and not failed_lines
            and not result.contains_error_words()
        )
        return DownloadOutcome(success=success, result=result, failed_lines=failed_lines)

    async def _run_streaming(
        self,
        args: list[str],
        *,
        timeout: float,
        on_progress: Callable[[DownloadProgress], None],
    ) -> PcsResult:
        """执行下载命令并逐行解析进度；stderr 合并进 stdout。"""

        started = time.monotonic()
        environment = self._environment()
        kwargs: dict[str, object] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
            "stdin": asyncio.subprocess.DEVNULL,
            "env": environment,
        }
        if _CREATE_NO_WINDOW:
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        try:
            process = await asyncio.create_subprocess_exec(str(self._executable), *args, **kwargs)
        except OSError as exc:
            raise PcsError("无法启动 BaiduPCS-Go，请检查文件权限。", detail=str(exc)) from exc
        assert process.stdout is not None

        collected: list[str] = []
        buffer = ""

        def emit(line: str) -> None:
            try:
                progress = _parse_progress(line)
            except ValueError:
                return
            if progress is not None:
                try:
                    on_progress(progress)
                except Exception:  # noqa: BLE001 - 进度回调不允许影响下载
                    pass

        async def reader() -> None:
            nonlocal buffer
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                text = _decode(chunk)
                collected.append(text)
                buffer += text
                parts = re.split(r"[\r\n]+", buffer)
                buffer = parts.pop()
                for line in parts:
                    emit(line)
            if buffer:
                emit(buffer)

        reader_task = asyncio.create_task(reader())
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.CancelledError:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            raise
        except asyncio.TimeoutError as exc:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            raise PcsTimeoutError("BaiduPCS-Go 下载超时，已终止进程。") from exc
        finally:
            try:
                await asyncio.wait_for(reader_task, timeout=5)
            except asyncio.CancelledError:
                reader_task.cancel()
                raise
            except Exception:  # noqa: BLE001 - 读取任务异常不影响结果
                reader_task.cancel()

        return PcsResult(
            code=process.returncode or 0,
            stdout="".join(collected),
            stderr="",
            duration=time.monotonic() - started,
        )

    async def rm(self, path: str) -> PcsResult:
        return await self.run(["rm", path], timeout=30)


def _login_failure_text(output: str) -> str:
    text = output.strip()
    if "验证码" in text:
        return "登录需要验证码，请改用 /pan qrlogin 扫码登录。"
    if "密码" in text and ("错误" in text or "不正确" in text):
        return "账号或密码错误。"
    return "百度网盘登录失败，请检查凭据或改用扫码登录。"


def _mask(text: str, keep: int = 6) -> str:
    stripped = text.strip()
    if len(stripped) <= keep * 2:
        return "***"
    return f"{stripped[:keep]}…{stripped[-keep:]}"


def _parse_progress(line: str) -> DownloadProgress | None:
    """解析 ``[0] ↓ 1.00MB/2.00MB 3.4MB/s in ...`` 形式的进度行。"""

    match = _PROGRESS_RE.search(line)
    if match is None:
        return None
    downloaded = _parse_size(match.group(1))
    total = _parse_size(match.group(2))
    if total <= 0:
        return None
    speed_match = _SPEED_RE.search(line)
    speed = f"{speed_match.group(1)}/s" if speed_match else ""
    return DownloadProgress(
        downloaded=downloaded,
        total=total,
        percent=min(100.0, downloaded / total * 100),
        speed=speed,
    )


def _parse_size(token: str) -> int:
    text = token.strip().upper()
    match = re.fullmatch(r"([\d.]+)\s*([KMGTP]?B?)", text)
    if match is None:
        raise ValueError(token)
    value = float(match.group(1))
    unit = match.group(2)
    multiplier = {
        "": 1,
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
        "PB": 1024**5,
    }.get(unit or "", 1)
    return int(value * multiplier)


__all__ = [
    "AccountInfo",
    "DownloadOutcome",
    "DownloadProgress",
    "PcsClient",
    "PcsResult",
    "RemoteEntry",
]
