"""百度网盘下载助手：MaiBot 插件入口。

本文件只负责生命周期、组件注册、权限入口与依赖装配；业务逻辑分散在
``baidu_pan`` 子包中，便于后续按功能扩展与单独测试。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

# 允许以包形式导入同目录的 baidu_pan 与 config.py（Runner 以文件方式加载插件）。
_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from maibot_sdk import CONFIG_RELOAD_SCOPE_SELF, Command, MaiBotPlugin  # noqa: E402

from baidu_pan.__version__ import PLUGIN_NAME, PLUGIN_VERSION  # noqa: E402
from baidu_pan.access import (  # noqa: E402
    AccessPolicy,
    Identity,
    WhitelistStore,
    extract_identity,
    parse_target_identity,
)
from baidu_pan.config_file import ensure_config_file  # noqa: E402
from baidu_pan.errors import BaiduPanError  # noqa: E402
from baidu_pan.logging_utils import configure_logging, detach_file_handler  # noqa: E402
from baidu_pan.texts import (  # noqa: E402
    INVALID_TARGET_TEXT,
    PLUGIN_DISABLED_TEXT,
    UNKNOWN_COMMAND_TEXT,
    build_help_text,
    status_text,
    whitelist_added_text,
    whitelist_exists_text,
    whitelist_list_text,
    whitelist_missing_text,
    whitelist_removed_text,
)
from baidu_pan.utils import normalize_account, split_multi  # noqa: E402
from config import BaiduPanConfig  # noqa: E402

logger = logging.getLogger(__name__)

_HELP_ALIASES = {"help", "h", "?", "帮助"}


class BaiduPanPlugin(MaiBotPlugin):
    """百度网盘下载助手插件。"""

    config_model = BaiduPanConfig

    def __init__(self) -> None:
        super().__init__()
        self._logger: logging.Logger = logging.getLogger("maibot_sdk.plugin")
        self._file_handler: logging.Handler | None = None
        self._whitelist: WhitelistStore | None = None
        self._unloaded = False

    # ------------------------------------------------------------------ 生命周期

    async def on_load(self) -> None:
        """插件加载：初始化日志、检查配置文件、载入白名单并记录摘要。"""

        self._logger = self.ctx.logger
        self._unloaded = False
        self._apply_logging()
        self._ensure_config_file()
        try:
            store = self._ensure_whitelist()
            self._logger.info("白名单已载入：%d 人（%s）", store.count(), store.path)
        except OSError as exc:
            self._logger.error("白名单载入失败：%s", exc, exc_info=True)
        self._log_runtime_summary()

    async def on_unload(self) -> None:
        """插件卸载：释放文件日志 handler，可重复调用。"""

        if self._unloaded:
            return
        self._unloaded = True
        detach_file_handler(self._logger)
        self._file_handler = None
        self._logger.info("%s v%s 已卸载", PLUGIN_NAME, PLUGIN_VERSION)

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """配置热更新：仅处理插件自身配置，刷新日志级别/文件输出。"""

        if scope != CONFIG_RELOAD_SCOPE_SELF:
            return
        self._apply_logging()
        self._logger.info("%s 配置已更新（config_version=%s）", PLUGIN_NAME, version)

    # ------------------------------------------------------------------ 内部方法

    def _apply_logging(self) -> None:
        """按当前配置设置日志级别与轮转文件日志。"""

        settings = self.config.logging
        file_path: Path | None = None
        if settings.file_enabled:
            file_path = Path(self.ctx.paths.data_dir) / "logs" / "baidu-pan.log"
        self._file_handler = configure_logging(
            self._logger,
            level=settings.level,
            file_path=file_path,
            max_bytes=settings.file_max_bytes,
            backup_count=settings.file_backup_count,
        )

    def _ensure_config_file(self) -> None:
        """兜底检查配置文件；Runner 未生成时按默认值创建。"""

        config_path = _PLUGIN_DIR / "config.toml"
        try:
            existed = ensure_config_file(config_path, self.build_default_config())
        except OSError as exc:
            self._logger.warning("检查配置文件失败（%s）：%s", config_path, exc)
            return
        if existed:
            self._logger.info("配置文件：%s", config_path)
        else:
            self._logger.warning("未找到配置文件，已按默认值创建：%s", config_path)

    def _ensure_whitelist(self) -> WhitelistStore:
        """惰性创建白名单存储（便于测试直接调用命令）。"""

        if self._whitelist is None:
            path = Path(self.ctx.paths.data_dir) / "state" / "whitelist.json"
            self._whitelist = WhitelistStore(path, self._logger)
        return self._whitelist

    def _access(self) -> AccessPolicy:
        return AccessPolicy(config=self.config, whitelist=self._ensure_whitelist())

    def _admin_count(self) -> int:
        entries = split_multi(self.config.access.admin_users)
        return sum(1 for entry in entries if normalize_account(entry) is not None)

    def _log_runtime_summary(self) -> None:
        """记录非敏感运行摘要，方便排查部署问题。"""

        access = self.config.access
        self._logger.info(
            "%s v%s 已加载：管理员=%d 人，白名单=%s，发送模式=%s，允许本地路径=%s",
            PLUGIN_NAME,
            PLUGIN_VERSION,
            self._admin_count(),
            "启用" if access.whitelist_enabled else "关闭",
            self.config.sending.mode,
            "是" if self.config.sending.local_path_enabled else "否",
        )

    # ------------------------------------------------------------------ 命令

    @Command(
        "pan",
        description="百度网盘下载助手：浏览分享、下载文件、管理白名单",
        pattern=r"^/pan(?:\s+(?P<args>[\s\S]*))?$",
    )
    async def handle_pan(self, **kwargs: Any) -> tuple[bool, str, int]:
        """``/pan`` 单入口命令，按子命令分发。"""

        if not self.config.plugin.enabled:
            return True, PLUGIN_DISABLED_TEXT, 0
        if self._unloaded:
            return False, "插件正在卸载，请稍后再试。", 0

        matched = kwargs.get("matched_groups") or {}
        args = str(matched.get("args") or "").strip()
        identity = extract_identity(kwargs)

        try:
            policy = self._access()
            if identity is not None and policy.is_blacklisted(identity):
                self._logger.info("黑名单用户 %s 的 /pan 请求已忽略", identity.key)
                return True, "", 0

            if not args:
                return True, build_help_text(), 2
            subcommand, rest = self._split(args)
            if subcommand in _HELP_ALIASES:
                return True, build_help_text(), 2

            handler = {
                "add": self._cmd_add,
                "del": self._cmd_del,
                "delete": self._cmd_del,
                "remove": self._cmd_del,
                "wlist": self._cmd_wlist,
                "whitelist": self._cmd_wlist,
                "status": self._cmd_status,
            }.get(subcommand)
            if handler is None:
                return True, UNKNOWN_COMMAND_TEXT, 2
            return await handler(identity, rest, kwargs)
        except BaiduPanError as exc:
            self._logger.info("/pan %s 被拒绝或失败：%s", args.split(maxsplit=1)[0] if args else "", exc)
            return True, str(exc), 2
        except Exception as exc:  # noqa: BLE001 - 命令入口必须兜底，避免宿主断链
            self._logger.error("/pan 命令处理异常：%s", exc, exc_info=True)
            return True, "操作失败，请稍后重试或查看插件日志。", 2

    # ------------------------------------------------------------------ 子命令实现

    @staticmethod
    def _split(args: str) -> tuple[str, str]:
        parts = args.split(maxsplit=1)
        return parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")

    async def _cmd_add(self, identity: Identity | None, rest: str, kwargs: dict[str, Any]) -> tuple[bool, str, int]:
        policy = self._access()
        policy.require_admin(identity)
        target = parse_target_identity(kwargs, rest)
        if target is None:
            return True, INVALID_TARGET_TEXT, 2
        store = self._ensure_whitelist()
        added_by = identity.key if identity is not None else ""
        if store.add(target, added_by=added_by):
            self._logger.info("管理员 %s 添加白名单 %s", added_by or "-", target.key)
            return True, whitelist_added_text(target.key, store.count()), 2
        return True, whitelist_exists_text(target.key), 2

    async def _cmd_del(self, identity: Identity | None, rest: str, kwargs: dict[str, Any]) -> tuple[bool, str, int]:
        policy = self._access()
        policy.require_admin(identity)
        target = parse_target_identity(kwargs, rest)
        if target is None:
            return True, INVALID_TARGET_TEXT, 2
        store = self._ensure_whitelist()
        if store.remove(target):
            self._logger.info("管理员 %s 移除白名单 %s", identity.key if identity else "-", target.key)
            return True, whitelist_removed_text(target.key, store.count()), 2
        return True, whitelist_missing_text(target.key), 2

    async def _cmd_wlist(self, identity: Identity | None, rest: str, kwargs: dict[str, Any]) -> tuple[bool, str, int]:
        policy = self._access()
        policy.require_admin(identity)
        store = self._ensure_whitelist()
        accounts = [f"{record['platform']}:{record['user_id']}" for record in store.list_users()]
        return True, whitelist_list_text(accounts), 2

    async def _cmd_status(self, identity: Identity | None, rest: str, kwargs: dict[str, Any]) -> tuple[bool, str, int]:
        policy = self._access()
        policy.require_manage(identity)
        return (
            True,
            status_text(
                admin_count=self._admin_count(),
                whitelist_count=self._ensure_whitelist().count(),
                whitelist_enabled=self.config.access.whitelist_enabled,
                sending_mode=self.config.sending.mode,
                pcs_version=self.config.download.pcs_version,
            ),
            2,
        )


def create_plugin() -> BaiduPanPlugin:
    """由 Runner 调用的插件工厂。"""

    return BaiduPanPlugin()


__all__ = ["BaiduPanPlugin", "create_plugin"]
