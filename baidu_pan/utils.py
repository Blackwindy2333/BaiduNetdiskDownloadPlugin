"""通用工具：格式化、账号解析、路径安全、原子 JSON。"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def now_iso() -> str:
    """返回本地时区的 ISO 时间字符串（秒精度）。"""

    return datetime.now().astimezone().isoformat(timespec="seconds")


def format_size(size: int | float | None) -> str:
    """把字节数格式化为人类可读的大小，使用 1024 进制。"""

    if size is None or size < 0:
        return "-"
    value = float(size)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} PB"  # pragma: no cover - 理论上不可达


def format_duration(seconds: int | float | None) -> str:
    """把秒数格式化为「1小时2分3秒」形式。"""

    if seconds is None or seconds < 0:
        return "-"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def split_multi(value: str | Iterable[str] | None) -> list[str]:
    """拆分逗号/分号/空白分隔的多值字符串并去重（保持顺序）。"""

    if value is None:
        return []
    if not isinstance(value, str):
        items: list[str] = []
        for part in value:
            items.extend(split_multi(str(part)))
        return items
    text = value
    for separator in ("，", "；", ";", "\n", "\r", "\t", " "):
        text = text.replace(separator, ",")
    return list(dict.fromkeys(item.strip() for item in text.split(",") if item.strip()))


def normalize_account(raw: str | None, *, default_platform: str = "qq") -> tuple[str, str] | None:
    """把 ``qq:123``、``123``、``@123`` 规范化为 ``(platform, user_id)``。"""

    if raw is None:
        return None
    text = str(raw).strip().lstrip("@").strip()
    if not text:
        return None
    if ":" in text:
        platform, user_id = text.split(":", 1)
        platform = platform.strip().lower()
        user_id = user_id.strip()
    else:
        platform, user_id = default_platform, text
    if platform in {"onebot", "napcat", "snowluma"}:
        platform = "qq"
    if not platform or not user_id:
        return None
    return platform, user_id


def account_matches(raw_entry: str | None, platform: str, user_id: str) -> bool:
    """判断配置中的某个账号条目是否匹配指定用户。"""

    parsed = normalize_account(raw_entry, default_platform=platform or "qq")
    if parsed is None:
        return False
    entry_platform, entry_user = parsed
    return entry_platform == (platform or "qq").lower() and entry_user == str(user_id)


def safe_filename(name: str, *, max_length: int = 120, fallback: str = "file") -> str:
    """生成跨平台安全的文件名（不含目录）。"""

    base = Path(str(name or "")).name
    base = _INVALID_FILENAME_CHARS.sub("_", base).strip().strip(".")
    if not base:
        base = fallback
    stem = Path(base).stem or fallback
    if stem.upper() in _RESERVED_WINDOWS_NAMES:
        base = f"_{base}"
    if len(base) > max_length:
        suffix = Path(base).suffix
        keep = max(1, max_length - len(suffix))
        base = (Path(base).stem[:keep] + suffix)[:max_length]
    return base


def atomic_write_json(path: Path, payload: Any, *, mode: int = 0o600) -> None:
    """原子写入 JSON：同目录临时文件 + fsync + os.replace。"""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def read_json(path: Path, default: Any = None) -> Any:
    """读取 JSON；文件缺失或损坏时返回 ``default``，不抛异常。"""

    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return default


def ensure_within(base: Path, target: Path) -> Path:
    """确保 ``target`` 位于 ``base`` 内，返回 resolve 后的路径。"""

    resolved_base = Path(base).resolve()
    resolved_target = Path(target).resolve()
    try:
        resolved_target.relative_to(resolved_base)
    except ValueError as exc:
        raise ValueError(f"目标路径越界：{target}") from exc
    return resolved_target


def truncate_text(text: str, limit: int, *, suffix: str = "…") -> str:
    """按字符数截断文本，附省略号。"""

    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def coerce_int(value: Any, default: int = 0, *, minimum: int | None = None, maximum: int | None = None) -> int:
    """把工具参数转成 int；空值/非法值返回默认值。"""

    if value is None or value == "":
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def coerce_bool(value: Any, default: bool = False) -> bool:
    """把工具参数转成 bool，兼容常见字符串写法。"""

    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "是", "开启"}


def build_job_id(prefix: str = "job") -> str:
    """生成短任务 ID。"""

    return f"{prefix}-{uuid.uuid4().hex[:12]}"


__all__ = [
    "account_matches",
    "atomic_write_json",
    "build_job_id",
    "coerce_bool",
    "coerce_int",
    "ensure_within",
    "format_duration",
    "format_size",
    "normalize_account",
    "now_iso",
    "read_json",
    "safe_filename",
    "split_multi",
    "truncate_text",
]
