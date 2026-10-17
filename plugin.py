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
from baidu_pan.config_file import ensure_config_file  # noqa: E402
from baidu_pan.logging_utils import configure_logging, detach_file_handler  # noqa: E402
from baidu_pan.texts import (  # noqa: E402
    PLUGIN_DISABLED_TEXT,
    UNKNOWN_COMMAND_TEXT,
    build_help_text,
)
from config import BaiduPanConfig  # noqa: E402

logger = logging.getLogger(__name__)


class BaiduPanPlugin(MaiBotPlugin):
    """百度网盘下载助手插件。"""

    config_model = BaiduPanConfig

    def __init__(self) -> None:
        super().__init__()
        self._logger: logging.Logger = logging.getLogger("maibot_sdk.plugin")
        self._file_handler: logging.Handler | None = None
        self._unloaded = False

    # ------------------------------------------------------------------ 生命周期

    async def on_load(self) -> None:
        """插件加载：初始化日志、检查配置文件并记录运行摘要。"""

        self._logger = self.ctx.logger
        self._unloaded = False
        self._apply_logging()
        self._ensure_config_file()
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

    def _log_runtime_summary(self) -> None:
        """记录非敏感运行摘要，方便排查部署问题。"""

        access = self.config.access
        admins = [item for item in access.admin_users.split(",") if item]
        self._logger.info(
            "%s v%s 已加载：管理员=%d 人，白名单=%s，发送模式=%s，允许本地路径=%s",
            PLUGIN_NAME,
            PLUGIN_VERSION,
            len(admins),
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
        """``/pan`` 单入口命令；子命令在后续提交中逐步接入。"""

        if not self.config.plugin.enabled:
            return True, PLUGIN_DISABLED_TEXT, 0
        if self._unloaded:
            return False, "插件正在卸载，请稍后再试。", 0

        matched = kwargs.get("matched_groups") or {}
        args = str(matched.get("args") or "").strip()
        if not args:
            return True, build_help_text(), 2

        subcommand = args.split(maxsplit=1)[0].lower()
        if subcommand in {"help", "h", "?", "帮助"}:
            return True, build_help_text(), 2
        return True, UNKNOWN_COMMAND_TEXT, 2


def create_plugin() -> BaiduPanPlugin:
    """由 Runner 调用的插件工厂。"""

    return BaiduPanPlugin()


__all__ = ["BaiduPanPlugin", "create_plugin"]
