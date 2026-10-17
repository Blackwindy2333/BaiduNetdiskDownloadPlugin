"""文件发送：Base64 / 共享路径两种传输与链接回退。

只使用 Host 标准能力 ``send.custom("file", payload)``；统一 QQ 连接器会根据
NapCat/SnowLuma 画像自行分派，插件不直接调用适配器 API。
"""

from __future__ import annotations

import base64
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable

from .models import SendOutcome
from .utils import format_size, safe_filename

_BASE64_HARD_LIMIT_BYTES = 9_500_000  # Host IPC 帧上限 10 MiB，留出消息包装余量


class FileSender:
    """把本地文件发送到聊天流，失败时按配置回退为链接文本。"""

    def __init__(
        self,
        send_proxy: Any,
        *,
        config_provider: Callable[[], Any],
        staging_dir_provider: Callable[[], Path],
        logger: logging.Logger | None = None,
        on_leftover: Callable[[Path], None] | None = None,
    ) -> None:
        self._send = send_proxy
        self._config_provider = config_provider
        self._staging_dir_provider = staging_dir_provider
        self._logger = logger or logging.getLogger(__name__)
        self._on_leftover = on_leftover

    async def send_text(self, stream_id: str, text: str) -> bool:
        try:
            return bool(await self._send.text(text, stream_id))
        except Exception as exc:  # noqa: BLE001 - 发送失败不抛出
            self._logger.warning("文本发送失败：%s", exc)
            return False

    async def send_file(
        self,
        stream_id: str,
        path: Path,
        *,
        display_name: str = "",
        share_url: str = "",
    ) -> SendOutcome:
        """按配置选择传输方式发送文件。"""

        file_path = Path(path)
        name = display_name.strip() or file_path.name
        try:
            size = file_path.stat().st_size
        except OSError as exc:
            return SendOutcome(False, "none", f"读取文件失败：{exc}")

        config = self._config_provider()
        sending = config.sending
        mode = sending.mode
        last_failure: SendOutcome | None = None

        if mode in {"auto", "base64"}:
            if size <= max(1, int(sending.base64_threshold_mb)) * 1024 * 1024:
                outcome = await self._send_base64(stream_id, file_path, name, size)
                if outcome.success:
                    return outcome
                last_failure = outcome
            elif mode == "base64":
                self._logger.info("文件超过 Base64 阈值：%s（%s）", name, format_size(size))

        if mode in {"auto", "local"} and sending.local_path_enabled:
            outcome = await self._send_local_path(stream_id, file_path, name, size)
            if outcome.success:
                return outcome
            last_failure = outcome

        fallback = await self._fallback(stream_id, file_path, name, size, share_url)
        if last_failure is not None:
            fallback.leftover_path = last_failure.leftover_path
            fallback.detail = f"{last_failure.detail}；{fallback.detail}"
        return fallback

    async def _send_base64(self, stream_id: str, path: Path, name: str, size: int) -> SendOutcome:
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as exc:
            return SendOutcome(False, "base64", f"读取文件失败：{exc}")
        if len(encoded) > _BASE64_HARD_LIMIT_BYTES:
            return SendOutcome(False, "base64", "Base64 内容超过 Host IPC 上限")
        payload = {
            "name": name,
            "size": str(size),
            "mime_type": _guess_mime(name),
            "url": f"base64://{encoded}",
        }
        return await self._send_payload(stream_id, payload, "base64", path, size)

    async def _send_local_path(self, stream_id: str, path: Path, name: str, size: int) -> SendOutcome:
        staging_dir = self._staging_dir_provider()
        try:
            staging_dir.mkdir(parents=True, exist_ok=True)
            suffix = path.suffix[:16]
            staged = staging_dir / f"upload-{uuid.uuid4().hex}{suffix}"
            shutil.copyfile(path, staged)
        except OSError as exc:
            return SendOutcome(False, "local", f"创建共享暂存副本失败：{exc}")

        payload = {
            "name": name,
            "size": str(size),
            "mime_type": _guess_mime(name),
            "url": staged.as_uri(),
        }
        outcome = await self._send_payload(stream_id, payload, "local", staged, size)
        if outcome.success:
            try:
                staged.unlink(missing_ok=True)
            except OSError as exc:
                self._logger.warning("删除暂存副本失败：%s", exc)
        else:
            outcome.leftover_path = str(staged)
            if self._on_leftover is not None:
                try:
                    self._on_leftover(staged)
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning("登记暂存副本失败：%s", exc)
        return outcome

    async def _send_payload(
        self,
        stream_id: str,
        payload: dict[str, Any],
        transport: str,
        source_path: Path,
        size: int,
    ) -> SendOutcome:
        try:
            details = await self._send.custom(
                "file",
                payload,
                stream_id,
                storage_message=False,
                show_log=False,
                sync_to_maisaka_history=False,
                return_details=True,
            )
        except Exception as exc:  # noqa: BLE001 - 适配器/宿主异常统一回退
            self._logger.warning("文件发送调用失败（%s）：%s", transport, exc)
            return SendOutcome(False, transport, f"发送调用异常：{exc}")
        success = isinstance(details, dict) and details.get("success") is True
        if success:
            self._logger.info(
                "文件已发送：%s（%s，%s）",
                payload.get("name"),
                format_size(size),
                transport,
            )
            return SendOutcome(True, transport, str(details.get("message_id") or ""))
        self._logger.warning("文件发送未确认成功：name=%s transport=%s result=%s", payload.get("name"), transport, details)
        return SendOutcome(False, transport, f"宿主未确认发送成功：{details!r}")

    async def _fallback(
        self,
        stream_id: str,
        path: Path,
        name: str,
        size: int,
        share_url: str,
    ) -> SendOutcome:
        config = self._config_provider()
        if not config.sending.link_fallback:
            return SendOutcome(False, "link", "发送失败且未启用链接回退")
        message = [
            f"⚠️ 文件「{name}」（{format_size(size)}）未能直接发送。",
        ]
        if share_url:
            message.append(f"分享链接：{share_url}")
        message.append(f"本地路径：{path}")
        sent = await self.send_text(stream_id, "\n".join(message))
        return SendOutcome(False, "link", "已回退为链接文本", fallback_used=sent)


def _guess_mime(name: str) -> str:
    import mimetypes

    guessed, _encoding = mimetypes.guess_type(safe_filename(name))
    return guessed or "application/octet-stream"


__all__ = ["FileSender"]
