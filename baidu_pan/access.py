"""权限、管理员白名单与发送者身份解析。

设计要点：

- 管理员名单来自 ``config.toml``（部署级、改动少）；
- 白名单存 ``data_dir/state/whitelist.json``（运行期、原子写、损坏可恢复）；
- 所有判定都显式接收当前配置，避免热更新后状态不一致。
"""

from __future__ import annotations

import logging
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import AdminNotConfiguredError, PermissionDeniedError
from .utils import (
    account_matches,
    atomic_write_json,
    normalize_account,
    now_iso,
    read_json,
    split_multi,
)

DEFAULT_PLATFORM = "qq"
_IDENTITY_KEYS = (
    ("user_id",),
    ("sender", "user_id"),
    ("message_info", "user_info", "user_id"),
    ("message_base_info", "user_info", "user_id"),
    ("raw_message", "user_id"),
    ("raw_message", "sender", "user_id"),
)


@dataclass(frozen=True, slots=True)
class Identity:
    """一次请求的发送者身份。"""

    platform: str
    user_id: str

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.user_id}"


def _get(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for key in path:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(key)
        else:
            current = getattr(current, key, None)
    return current


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def extract_identity(kwargs: dict[str, Any]) -> Identity | None:
    """从命令/工具 kwargs 中尽力提取发送者身份。

    不同适配器/宿主版本对 ``message`` 的结构略有差异，因此按常见路径逐项尝试；
    提取失败返回 ``None``，调用方对下载类请求应明确拒绝而不是猜测。
    """

    message = kwargs.get("message")
    platform = _first_text(
        kwargs.get("platform"),
        _get(message, ("platform",)),
        _get(message, ("message_base_info", "platform")),
        DEFAULT_PLATFORM,
    ).lower()
    if platform in {"onebot", "napcat", "snowluma"}:
        platform = DEFAULT_PLATFORM

    user_id = ""
    peers = [kwargs, message, _get(message, ("raw_message",))]
    for source in peers:
        if source is None:
            continue
        for path in _IDENTITY_KEYS:
            candidate = _get(source, path)
            if candidate is not None and str(candidate).strip():
                user_id = str(candidate).strip()
                break
        if user_id:
            break

    if not user_id:
        return None
    return Identity(platform=platform or DEFAULT_PLATFORM, user_id=user_id)


def _message_value(kwargs: dict[str, Any]) -> Any:
    message = kwargs.get("message")
    if isinstance(message, (dict, list)) or message is None:
        return message
    return message


def extract_at_target(kwargs: dict[str, Any]) -> Identity | None:
    """从消息的 ``at`` 段中提取被 @ 的成员。"""

    message = _message_value(kwargs)
    candidates: list[Any] = []
    if isinstance(message, dict):
        candidates.extend(
            [
                message.get("message_segments"),
                message.get("segments"),
                message.get("message"),
                _get(message, ("raw_message", "message")),
                _get(message, ("raw_message", "message_segments")),
            ]
        )
    elif isinstance(message, list):
        candidates.append(message)

    for segments in candidates:
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            if str(segment.get("type", "")).lower() not in {"at", "mention"}:
                continue
            data = segment.get("data") or {}
            raw_id = _first_text(data.get("qq"), data.get("user_id"), data.get("id"))
            if raw_id and raw_id != "all":
                parsed = normalize_account(raw_id, default_platform=DEFAULT_PLATFORM)
                if parsed is not None:
                    return Identity(platform=parsed[0], user_id=parsed[1])
    return None


def parse_target_identity(kwargs: dict[str, Any], raw: str) -> Identity | None:
    """解析命令参数中的目标账号：优先 ``@`` 段，其次纯数字/``qq:123``。"""

    at_target = extract_at_target(kwargs)
    text = (raw or "").strip()
    if at_target is not None:
        return at_target
    token = text.split(maxsplit=1)[0] if text else ""
    if not token:
        return None
    parsed = normalize_account(token.lstrip("@"), default_platform=DEFAULT_PLATFORM)
    if parsed is None:
        return None
    return Identity(platform=parsed[0], user_id=parsed[1])


class WhitelistStore:
    """白名单持久化：原子写、损坏备份、进程内互斥。"""

    def __init__(self, path: Path, logger: logging.Logger | None = None) -> None:
        self._path = Path(path)
        self._logger = logger or logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._users: dict[str, dict[str, str]] = {}
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    def _identity_key(self, platform: str, user_id: str) -> str:
        return f"{(platform or DEFAULT_PLATFORM).lower()}:{str(user_id).strip()}"

    def load(self) -> None:
        """从磁盘载入；文件缺失时创建空文件，损坏时备份后重建。"""

        payload = read_json(self._path, None)
        if payload is None:
            if self._path.exists():
                backup = self._path.with_name(f"{self._path.name}.corrupt-{now_iso().replace(':', '-')}.bak")
                try:
                    shutil.copy2(self._path, backup)
                    self._logger.warning("白名单文件损坏，已备份到 %s 并重建", backup)
                except OSError as exc:  # pragma: no cover - 极端文件系统错误
                    self._logger.warning("白名单文件损坏且备份失败：%s", exc)
            self._users = {}
            self._persist_locked()
            return

        users: dict[str, dict[str, str]] = {}
        raw_users = payload.get("users") if isinstance(payload, dict) else payload
        if isinstance(raw_users, list):
            for item in raw_users:
                if isinstance(item, str):
                    parsed = normalize_account(item, default_platform=DEFAULT_PLATFORM)
                    if parsed is None:
                        continue
                    platform, user_id = parsed
                    record = {"platform": platform, "user_id": user_id}
                elif isinstance(item, dict):
                    platform = str(item.get("platform") or DEFAULT_PLATFORM).lower()
                    user_id = str(item.get("user_id") or item.get("id") or "").strip()
                    if not user_id:
                        continue
                    record = {
                        "platform": platform,
                        "user_id": user_id,
                        "added_by": str(item.get("added_by") or ""),
                        "added_at": str(item.get("added_at") or ""),
                    }
                else:
                    continue
                key = self._identity_key(record["platform"], record["user_id"])
                record.setdefault("added_at", "")
                users[key] = record
        self._users = users

    def _persist_locked(self) -> None:
        payload = {
            "version": 1,
            "updated_at": now_iso(),
            "users": list(self._users.values()),
        }
        atomic_write_json(self._path, payload)

    def _persist(self) -> None:
        with self._lock:
            self._persist_locked()

    def add(self, identity: Identity, *, added_by: str = "") -> bool:
        """添加白名单；已存在返回 ``False``。"""

        normalized = normalize_account(identity.user_id, default_platform=identity.platform)
        if normalized is None:
            return False
        platform, user_id = normalized
        key = self._identity_key(platform, user_id)
        with self._lock:
            if key in self._users:
                return False
            self._users[key] = {
                "platform": platform,
                "user_id": user_id,
                "added_by": added_by,
                "added_at": now_iso(),
            }
            self._persist_locked()
        return True

    def remove(self, identity: Identity) -> bool:
        """删除白名单；不存在返回 ``False``。"""

        normalized = normalize_account(identity.user_id, default_platform=identity.platform)
        if normalized is None:
            return False
        key = self._identity_key(*normalized)
        with self._lock:
            if key not in self._users:
                return False
            del self._users[key]
            self._persist_locked()
        return True

    def contains(self, identity: Identity) -> bool:
        normalized = normalize_account(identity.user_id, default_platform=identity.platform)
        if normalized is None:
            return False
        key = self._identity_key(*normalized)
        with self._lock:
            return key in self._users

    def list_users(self) -> list[dict[str, str]]:
        with self._lock:
            return [
                dict(record)
                for record in sorted(self._users.values(), key=lambda item: (item.get("added_at", ""), item.get("user_id", "")))
            ]

    def count(self) -> int:
        with self._lock:
            return len(self._users)


@dataclass(slots=True)
class AccessPolicy:
    """基于「当前配置 + 白名单」的权限判定。"""

    config: Any
    whitelist: WhitelistStore

    def _admin_entries(self) -> list[str]:
        return split_multi(getattr(self.config.access, "admin_users", ""))

    def _blacklist_entries(self) -> list[str]:
        return split_multi(getattr(self.config.access, "blacklist", ""))

    def admin_configured(self) -> bool:
        return any(normalize_account(entry) is not None for entry in self._admin_entries())

    def is_blacklisted(self, identity: Identity | None) -> bool:
        if identity is None:
            return False
        return any(
            account_matches(entry, identity.platform, identity.user_id)
            for entry in self._blacklist_entries()
        )

    def is_admin(self, identity: Identity | None) -> bool:
        if identity is None:
            return False
        return any(
            account_matches(entry, identity.platform, identity.user_id)
            for entry in self._admin_entries()
        )

    def can_download(self, identity: Identity | None) -> bool:
        if identity is None or self.is_blacklisted(identity):
            return False
        if self.is_admin(identity):
            return True
        if not getattr(self.config.access, "whitelist_enabled", True):
            return True
        if not self.admin_configured():
            return False
        return self.whitelist.contains(identity)

    def can_manage(self, identity: Identity | None) -> bool:
        if self.is_admin(identity):
            return True
        if getattr(self.config.access, "admin_only_sensitive", True):
            return False
        return self.can_download(identity)

    # -- 抛错版本，供命令/工具统一捕获 ---------------------------------

    def require_download(self, identity: Identity | None) -> None:
        if self.is_blacklisted(identity):
            raise PermissionDeniedError()
        if self.can_download(identity):
            return
        if not self.admin_configured():
            raise AdminNotConfiguredError(
                "尚未配置管理员账号，暂时无人可以下载。请管理员先在插件后台配置 access.admin_users。"
            )
        raise PermissionDeniedError("你没有下载权限，请联系管理员将你加入白名单。")

    def require_admin(self, identity: Identity | None) -> None:
        if self.is_blacklisted(identity):
            raise PermissionDeniedError()
        if not self.admin_configured():
            raise AdminNotConfiguredError()
        if not self.is_admin(identity):
            raise PermissionDeniedError("该命令仅管理员可用。")

    def require_manage(self, identity: Identity | None) -> None:
        if self.is_blacklisted(identity):
            raise PermissionDeniedError()
        if self.can_manage(identity):
            return
        if not self.admin_configured():
            raise AdminNotConfiguredError()
        raise PermissionDeniedError("该命令仅管理员可用。")


__all__ = [
    "AccessPolicy",
    "Identity",
    "WhitelistStore",
    "extract_at_target",
    "extract_identity",
    "parse_target_identity",
]
