"""日志配置与脱敏工具。

插件使用 Runner 注入的 ``ctx.logger``（标准库 Logger，名称 ``plugin.<id>``），
本模块只在其上追加/替换插件自己的轮转文件 handler，不修改宿主日志配置。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

_FILE_HANDLER_MARK = "_baidu_pan_file_handler"
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configure_logging(
    logger: logging.Logger,
    *,
    level: str = "INFO",
    file_path: Path | str | None = None,
    max_bytes: int = 2 * 1024 * 1024,
    backup_count: int = 3,
) -> RotatingFileHandler | None:
    """设置日志级别，并按需挂载轮转文件 handler。

    Args:
        logger: Runner 注入的插件 Logger。
        level: ``DEBUG``/``INFO``/``WARNING``/``ERROR``。
        file_path: 日志文件路径；``None`` 表示只使用宿主日志。
        max_bytes: 单文件大小上限。
        backup_count: 轮转保留个数。

    Returns:
        新建的文件 handler；未启用文件日志时为 ``None``。
    """

    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    detach_file_handler(logger)
    if file_path is None:
        return None

    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=max(65536, int(max_bytes)),
        backupCount=max(0, int(backup_count)),
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    setattr(handler, _FILE_HANDLER_MARK, True)
    logger.addHandler(handler)
    return handler


def detach_file_handler(logger: logging.Logger) -> None:
    """移除并关闭本插件挂载过的文件 handler，可重复调用。"""

    for handler in list(logger.handlers):
        if getattr(handler, _FILE_HANDLER_MARK, False):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # noqa: BLE001 - 卸载阶段不允许抛错
                pass


def mask_secret(value: str | None, *, keep: int = 2) -> str:
    """脱敏显示凭据：保留首尾少量字符，其余替换为 ``***``。"""

    text = str(value or "")
    if not text:
        return ""
    if len(text) <= keep * 2:
        return "***"
    return f"{text[:keep]}***{text[-keep:]}"


def redact_text(text: str, secrets: Iterable[str]) -> str:
    """把文本中出现的敏感串替换为 ``***``（用于日志与错误摘要）。"""

    result = str(text)
    for secret in secrets:
        if secret and len(str(secret)) >= 4:
            result = result.replace(str(secret), "***")
    return result


__all__ = ["configure_logging", "detach_file_handler", "mask_secret", "redact_text"]
