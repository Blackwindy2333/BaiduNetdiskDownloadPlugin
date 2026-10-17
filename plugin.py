"""百度网盘下载助手：MaiBot 插件入口。

本文件只负责生命周期、组件注册、权限入口与依赖装配；业务逻辑分散在
``baidu_pan`` 子包中，便于后续按功能扩展与单独测试。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
from pathlib import Path
from typing import Any

# 允许以包形式导入同目录的 baidu_pan 与 config.py（Runner 以文件方式加载插件）。
_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from maibot_sdk import CONFIG_RELOAD_SCOPE_SELF, Command, MaiBotPlugin, Tool  # noqa: E402
from maibot_sdk.types import ToolParamType, ToolParameterInfo  # noqa: E402

from baidu_pan.__version__ import PLUGIN_NAME, PLUGIN_VERSION  # noqa: E402
from baidu_pan.access import (  # noqa: E402
    AccessPolicy,
    Identity,
    WhitelistStore,
    extract_identity,
    parse_target_identity,
)
from baidu_pan.account import load_cookie_header  # noqa: E402
from baidu_pan.cleanup import CleanupScheduler, FileManifest  # noqa: E402
from baidu_pan.config_file import ensure_config_file  # noqa: E402
from baidu_pan.download_manager import DownloadManager, DownloadRequest  # noqa: E402
from baidu_pan.errors import (  # noqa: E402
    AuthError,
    BaiduPanError,
    DownloadError,
    PcsError,
    PermissionDeniedError,
)
from baidu_pan.logging_utils import configure_logging, detach_file_handler  # noqa: E402
from baidu_pan.pcs_client import AccountInfo, PcsClient  # noqa: E402
from baidu_pan.pcs_installer import PcsInstaller, binary_name  # noqa: E402
from baidu_pan.qrlogin import QrLoginSession  # noqa: E402
from baidu_pan.models import ShareNode, ShareTree  # noqa: E402
from baidu_pan.sender import FileSender  # noqa: E402
from baidu_pan.share import (  # noqa: E402
    ShareClient,
    ShareSessionCache,
    parse_share_input,
    render_tree,
)
from baidu_pan.transfer import (  # noqa: E402
    TransferResult,
    root_nodes,
    select_node,
    transfer_nodes,
)
from baidu_pan.texts import (  # noqa: E402
    INVALID_TARGET_TEXT,
    NO_SHARE_SESSION_TEXT,
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
from baidu_pan.utils import (  # noqa: E402
    build_job_id,
    format_size,
    normalize_account,
    split_multi,
    truncate_text,
)
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
        self._share_cache = ShareSessionCache()
        self._pcs_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._qr_task: asyncio.Task[Any] | None = None
        self._sender: FileSender | None = None
        self._manifest: FileManifest | None = None
        self._downloads: DownloadManager | None = None
        self._cleanup: CleanupScheduler | None = None
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
        self._ensure_runtime()
        self._log_runtime_summary()
        if self._cleanup is not None:
            await self._cleanup.start()
        if self.config.account.auto_login and self.config.account.bduss:
            self._logger.info("检测到自动登录配置，开始后台登录")
            self._spawn(self._auto_login(), name="auto-login")

    async def on_unload(self) -> None:
        """插件卸载：取消后台任务并释放文件日志 handler，可重复调用。"""

        if self._unloaded:
            return
        self._unloaded = True
        if self._cleanup is not None:
            try:
                await self._cleanup.stop()
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("停止清理任务时出现异常：%s", exc)
            self._cleanup = None
        if self._downloads is not None:
            try:
                await self._downloads.shutdown()
            except Exception as exc:  # noqa: BLE001 - 卸载不抛错
                self._logger.warning("停止下载任务时出现异常：%s", exc)
            self._downloads = None
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        self._qr_task = None
        detach_file_handler(self._logger)
        self._file_handler = None
        self._logger.info("%s v%s 已卸载", PLUGIN_NAME, PLUGIN_VERSION)

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """配置热更新：仅处理插件自身配置，刷新日志级别/文件输出。"""

        if scope != CONFIG_RELOAD_SCOPE_SELF:
            return
        self._apply_logging()
        if self._downloads is not None:
            self._downloads.set_download_root(self._download_root())
        if self._cleanup is not None:
            await self._cleanup.restart()
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

    # ------------------------------------------------------------------ 后台任务与 BaiduPCS-Go

    def _spawn(self, coro: Any, *, name: str = "") -> asyncio.Task[Any]:
        """创建后台任务并纳入卸载清理。"""

        task = asyncio.create_task(coro, name=name or None)
        self._tasks.add(task)
        task.add_done_callback(self._handle_task_done)
        return task

    def _handle_task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task is self._qr_task:
            self._qr_task = None
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._logger.error(
                "后台任务异常：%s",
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    def _install_dir(self) -> Path:
        return Path(self.ctx.paths.data_dir) / "bin"

    def _pcs_config_dir(self) -> Path:
        env_dir = os.environ.get("BAIDUPCS_GO_CONFIG_DIR", "").strip()
        if env_dir:
            return Path(env_dir)
        custom = self.config.download.baidu_pcs_go_path.strip()
        if custom:
            return Path(custom).expanduser().resolve().parent
        return self._install_dir()

    def _pcs_executable(self) -> Path:
        custom = self.config.download.baidu_pcs_go_path.strip()
        if custom:
            return Path(custom).expanduser()
        return self._install_dir() / binary_name()

    def _make_pcs_client(self) -> PcsClient:
        return PcsClient(
            self._pcs_executable(),
            config_dir=self._pcs_config_dir(),
            logger=self._logger,
        )

    def _make_installer(self, *, direct_only: bool = False) -> PcsInstaller:
        prefixes: list[str] = []
        if not direct_only:
            prefixes = [prefix.strip() for prefix in split_multi(self.config.download.pcs_download_prefixes)]
        return PcsInstaller(
            self._install_dir(),
            prefixes=prefixes,
            verify_checksum=self.config.download.pcs_verify_checksum,
            logger=self._logger,
        )

    async def _ensure_pcs(self, *, force: bool = False, direct_only: bool = False) -> Path:
        """确保 BaiduPCS-Go 可用，返回可执行文件路径。"""

        custom = self.config.download.baidu_pcs_go_path.strip()
        if custom:
            path = Path(custom).expanduser()
            if not path.is_file():
                raise PcsError(f"配置的 BaiduPCS-Go 路径不存在：{path}")
            return path
        async with self._pcs_lock:
            installer = self._make_installer(direct_only=direct_only)
            return await installer.ensure(self.config.download.pcs_version, force=force)

    async def _auto_login(self) -> None:
        try:
            client = self._make_pcs_client()
            account = await client.login_bduss(self.config.account.bduss, self.config.account.stoken)
            self._logger.info("自动登录成功：%s", account.name if account else "未知账号")
        except BaiduPanError as exc:
            self._logger.warning("自动登录失败：%s", exc)
        except Exception as exc:  # noqa: BLE001 - 后台任务必须自吞异常
            self._logger.error("自动登录异常：%s", exc, exc_info=True)

    async def _send_text(self, stream_id: str, text: str) -> None:
        if not stream_id:
            self._logger.warning("缺少 stream_id，无法发送后台消息：%s", text[:80])
            return
        try:
            await self.ctx.send.text(text, stream_id)
        except Exception as exc:  # noqa: BLE001 - 发送失败只记录
            self._logger.warning("后台消息发送失败：%s", exc)

    def _download_root(self) -> Path:
        custom = self.config.download.download_dir.strip()
        if custom:
            return Path(custom).expanduser()
        return Path(self.ctx.paths.data_dir) / "downloads"

    def _staging_dir(self) -> Path:
        custom = self.config.sending.staging_dir.strip()
        if custom:
            return Path(custom).expanduser()
        return Path(self.ctx.paths.data_dir) / "upload_cache"

    def _ensure_runtime(self) -> None:
        """创建文件发送器、清单与下载管理器（幂等）。"""

        if self._sender is not None:
            return
        data_dir = Path(self.ctx.paths.data_dir)
        self._manifest = FileManifest(data_dir / "state" / "cleanup_manifest.json", self._logger)
        self._sender = FileSender(
            self.ctx.send,
            config_provider=lambda: self.config,
            staging_dir_provider=self._staging_dir,
            logger=self._logger,
            on_leftover=lambda path: self._manifest.add([path]) if self._manifest is not None else None,
        )
        self._downloads = DownloadManager(
            config_provider=lambda: self.config,
            pcs_factory=self._make_pcs_client,
            sender=self._sender,
            notify=self._send_text,
            download_root=self._download_root(),
            manifest=self._manifest,
            logger=self._logger,
            max_concurrent=self.config.download.max_concurrent_jobs,
        )
        self._cleanup = CleanupScheduler(
            manifest=self._manifest,
            roots_provider=lambda: [self._download_root(), self._staging_dir()],
            hour_provider=lambda: int(self.config.download.cleanup_hour),
            retention_provider=lambda: int(self.config.download.retention_hours),
            logger=self._logger,
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
                "ls": self._cmd_ls,
                "look": self._cmd_look,
                "install": self._cmd_install,
                "download": self._cmd_install,
                "qrlogin": self._cmd_qrlogin,
                "bduss": self._cmd_bduss,
                "cookies": self._cmd_cookies,
                "login": self._cmd_login,
                "unlogin": self._cmd_unlogin,
                "file": self._cmd_file,
                "f": self._cmd_file,
                "dir": self._cmd_dir,
                "folder": self._cmd_dir,
                "fl": self._cmd_dir,
            }.get(subcommand)
            if handler is not None:
                return await handler(identity, rest, kwargs)
            if self._looks_like_share(args):
                return await self._cmd_browse(identity, args, kwargs)
            return True, UNKNOWN_COMMAND_TEXT, 2
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

    # ------------------------------------------------------------------ 分享浏览

    @staticmethod
    def _looks_like_share(text: str) -> bool:
        try:
            parse_share_input(text)
        except BaiduPanError:
            return False
        return True

    @staticmethod
    def _stream_key(kwargs: dict[str, Any]) -> str:
        stream_id = str(kwargs.get("stream_id") or "").strip()
        if stream_id:
            return stream_id
        identity = extract_identity(kwargs)
        return identity.key if identity is not None else "default"

    def _open_share_client(self) -> ShareClient:
        """创建分享客户端；未登录时也允许匿名浏览公开分享。"""

        cookies = ""
        try:
            cookies = load_cookie_header(self.config.download.baidu_pcs_go_path)
        except BaiduPanError as exc:
            self._logger.debug("未读取到百度网盘登录信息，将匿名浏览分享：%s", exc)
        return ShareClient(cookies, timeout=20.0, logger=self._logger)

    async def _browse_tree(self, raw: str, kwargs: dict[str, Any]) -> ShareTree:
        """解析分享并构建目录树，同时写入当前会话缓存。"""

        surl, pwd = parse_share_input(raw)
        client = self._open_share_client()
        try:
            info = await client.resolve(surl, pwd)
            tree = await client.build_tree(
                info,
                max_depth=self.config.share.max_depth,
                max_items=self.config.share.max_items,
                entries_per_dir=self.config.share.entries_per_dir,
            )
        finally:
            await client.aclose()
        self._share_cache.put(self._stream_key(kwargs), tree)
        self._logger.info("已浏览分享 %s：%d 个节点", info.share_id, len(tree.nodes))
        return tree

    async def _tree_for(self, link: str, pwd: str, kwargs: dict[str, Any]) -> ShareTree:
        """优先使用命令参数中的链接，否则读取当前会话缓存。"""

        if link.strip():
            return await self._browse_tree(f"{link.strip()} {pwd}".strip(), kwargs)
        tree = self._share_cache.get(self._stream_key(kwargs), self.config.share.cache_ttl_minutes * 60)
        if tree is None:
            raise BaiduPanError(NO_SHARE_SESSION_TEXT)
        return tree

    async def _cmd_browse(self, identity: Identity | None, raw: str, kwargs: dict[str, Any]) -> tuple[bool, str, int]:
        """``/pan <链接> [提取码]``：解析并缓存当前分享目录树。"""

        tree = await self._browse_tree(raw, kwargs)
        self._logger.info(
            "浏览分享用户=%s，会话=%s",
            identity.key if identity is not None else "-",
            self._stream_key(kwargs),
        )
        return True, render_tree(tree, max_entries=self.config.share.entries_per_dir), 2

    async def _cmd_ls(self, identity: Identity | None, rest: str, kwargs: dict[str, Any]) -> tuple[bool, str, int]:
        """``/pan ls`` 刷新当前分享，或 ``/pan ls <链接>`` 直接浏览。"""

        if rest and self._looks_like_share(rest):
            return await self._cmd_browse(identity, rest, kwargs)
        key = self._stream_key(kwargs)
        tree = self._share_cache.get(key, self.config.share.cache_ttl_minutes * 60)
        if tree is None:
            return True, NO_SHARE_SESSION_TEXT, 2
        client = self._open_share_client()
        try:
            info = await client.resolve(tree.info.surl, tree.info.pwd)
            refreshed = await client.build_tree(
                info,
                max_depth=self.config.share.max_depth,
                max_items=self.config.share.max_items,
                entries_per_dir=self.config.share.entries_per_dir,
            )
        finally:
            await client.aclose()
        self._share_cache.put(key, refreshed)
        return True, render_tree(refreshed, max_entries=self.config.share.entries_per_dir), 2

    async def _cmd_look(self, identity: Identity | None, rest: str, kwargs: dict[str, Any]) -> tuple[bool, str, int]:
        """``/pan look`` 查看当前分享的缓存目录树。"""

        tree = self._share_cache.get(self._stream_key(kwargs), self.config.share.cache_ttl_minutes * 60)
        if tree is None:
            return True, NO_SHARE_SESSION_TEXT, 2
        return True, render_tree(tree, max_entries=self.config.share.entries_per_dir), 2

    # ------------------------------------------------------------------ 转存

    async def _transfer_selected(self, tree: ShareTree, nodes: list[Any]) -> TransferResult:
        """执行转存；自家分享自动改为原路径直下，并可选做转存后校验。"""

        account_uid: int | None = None
        if self._pcs_executable().is_file():
            try:
                pcs = self._make_pcs_client()
                account = await pcs.who()
                account_uid = account.uid if account is not None else None
                try:
                    await pcs.mkdir(self.config.transfer.cloud_save_dir)
                except BaiduPanError as exc:
                    self._logger.debug("创建云端转存目录失败（可能已存在）：%s", exc)
            except BaiduPanError as exc:
                self._logger.debug("读取百度账号 uid 失败（不影响转存）：%s", exc)

        client = self._open_share_client()
        try:
            result = await transfer_nodes(
                client,
                tree,
                nodes,
                target_dir=self.config.transfer.cloud_save_dir,
                account_uid=account_uid,
            )
        finally:
            await client.aclose()

        if not result.own_share:
            self._logger.info("已转存 %d 项到 %s", len(result.names), result.target_dir)
            if self.config.transfer.verify_after_transfer:
                await self._verify_transferred(result)
        else:
            self._logger.info("分享属于当前账号，跳过转存，直接使用原路径")
        return result

    async def _verify_transferred(self, result: TransferResult) -> None:
        """转存后复查目标文件是否存在；失败只告警，不阻断后续下载。"""

        pcs = self._make_pcs_client()
        if not pcs.available:
            return
        try:
            entries = await pcs.ls(result.target_dir)
        except BaiduPanError as exc:
            self._logger.warning("转存后校验失败（不影响下载）：%s", exc)
            return
        existing = {entry.name for entry in entries}
        expected = {Path(name).name for name in result.filenames}
        missing = expected - existing
        if missing:
            self._logger.warning("转存后未在目标目录找到：%s", "、".join(sorted(missing)))

    # ------------------------------------------------------------------ 下载

    @staticmethod
    def _share_url(tree: ShareTree) -> str:
        base = f"https://pan.baidu.com/s/{tree.info.surl}"
        return f"{base}?pwd={tree.info.pwd}" if tree.info.pwd else base

    @staticmethod
    def _node_total_size(tree: ShareTree, node: ShareNode) -> int:
        if not node.is_dir:
            return node.size
        prefix = node.path.rstrip("/") + "/"
        return sum(
            item.size
            for item in tree.nodes
            if item.path == node.path or item.path.startswith(prefix)
        )

    def _size_limit_error(self, size: int, label: str) -> str | None:
        limit_mb = int(self.config.transfer.max_file_size_mb)
        if limit_mb <= 0:
            return None
        limit = limit_mb * 1024 * 1024
        if size > limit:
            return f"{label}大小 {format_size(size)} 超过配置上限 {limit_mb}MB，已拒绝处理。"
        return None

    def _start_download(
        self,
        tree: ShareTree,
        nodes: list[ShareNode],
        kwargs: dict[str, Any],
        identity: Identity | None,
        *,
        display_name: str = "",
    ) -> str:
        """创建后台下载流程，返回任务 ID。"""

        self._ensure_runtime()
        job_id = build_job_id("dl")
        name = display_name or (nodes[0].name if len(nodes) == 1 else f"{len(nodes)} 项内容")
        self._spawn(
            self._download_flow(job_id, tree, nodes, kwargs, identity, name),
            name=f"download-{job_id}",
        )
        return job_id

    async def _download_flow(
        self,
        job_id: str,
        tree: ShareTree,
        nodes: list[ShareNode],
        kwargs: dict[str, Any],
        identity: Identity | None,
        display_name: str,
    ) -> None:
        """后台流程：登录检查 → 转存 → 交给 DownloadManager 下载并发送。"""

        stream_id = str(kwargs.get("stream_id") or "")
        expected_size = sum(self._node_total_size(tree, node) for node in nodes)
        try:
            await self._ensure_pcs()
            pcs = self._make_pcs_client()
            account = await pcs.who()
            if account is None:
                raise AuthError("尚未登录百度网盘，请先使用 /pan qrlogin 登录。")
            await self._send_text(stream_id, f"⏳ 正在转存「{display_name}」…")
            transfer = await self._transfer_selected(tree, nodes)
            if transfer.own_share:
                cloud_paths = transfer.direct_paths
                cleanup_paths: list[str] = []
            else:
                target = transfer.target_dir.rstrip("/")
                cloud_paths = [f"{target}/{Path(name).name}" for name in transfer.filenames]
                cleanup_paths = [transfer.target_dir] if self.config.transfer.auto_delete_cloud else []
            expected_names = [Path(path).name for path in cloud_paths]
            request = DownloadRequest(
                job_id=job_id,
                stream_id=stream_id,
                platform=identity.platform if identity is not None else "qq",
                user_id=identity.user_id if identity is not None else "",
                display_name=display_name,
                cloud_paths=cloud_paths,
                cleanup_cloud_paths=cleanup_paths,
                share_url=self._share_url(tree),
                expected_size=expected_size,
                expected_names=expected_names,
                is_directory=any(node.is_dir for node in nodes),
            )
            if self._downloads is None:
                raise DownloadError("下载管理器尚未初始化。")
            self._downloads.submit(request)
        except BaiduPanError as exc:
            self._logger.warning("下载准备失败：job=%s %s", job_id, exc)
            await self._send_text(stream_id, f"❌ {display_name} 处理失败：{exc}")
        except Exception as exc:  # noqa: BLE001 - 后台任务自吞异常
            self._logger.error("下载准备异常：job=%s %s", job_id, exc, exc_info=True)
            await self._send_text(stream_id, f"❌ {display_name} 处理失败，请查看插件日志。")

    async def _cmd_file(self, identity: Identity | None, rest: str, kwargs: dict[str, Any]) -> tuple[bool, str, int]:
        """``/pan file <路径>``：下载单个文件。"""

        policy = self._access()
        policy.require_download(identity)
        if not rest.strip():
            return True, "用法：/pan file <目录树中的文件路径>", 2
        tree = await self._tree_for("", "", kwargs)
        node = select_node(tree, rest.strip())
        if node.is_dir:
            return True, f"「{node.name}」是目录，请使用 /pan dir {node.path}。", 2
        error = self._size_limit_error(node.size, "文件")
        if error:
            return True, error, 2
        job_id = self._start_download(tree, [node], kwargs, identity)
        return True, f"✅ 已受理文件下载：{node.name}（{format_size(node.size)}，任务 {job_id}）。完成后会自动发送。", 2

    async def _cmd_dir(self, identity: Identity | None, rest: str, kwargs: dict[str, Any]) -> tuple[bool, str, int]:
        """``/pan dir <路径>``：下载整个目录。"""

        policy = self._access()
        policy.require_download(identity)
        if not rest.strip():
            return True, "用法：/pan dir <目录树中的目录路径>", 2
        tree = await self._tree_for("", "", kwargs)
        node = select_node(tree, rest.strip())
        if not node.is_dir:
            return True, f"「{node.name}」是文件，请使用 /pan file {node.path}。", 2
        size = self._node_total_size(tree, node)
        error = self._size_limit_error(size, "目录")
        if error:
            return True, error, 2
        job_id = self._start_download(tree, [node], kwargs, identity)
        return True, f"✅ 已受理目录下载：{node.name}（{format_size(size)}，任务 {job_id}）。完成后会发送文件。", 2

    # ------------------------------------------------------------------ LLM 工具

    @Tool(
        "pan_list",
        brief_description="浏览百度网盘分享的目录结构",
        detailed_description=(
            "当用户发来百度网盘分享链接并询问“里面有什么/列目录/看看文件”时调用。"
            "参数：link 为分享链接或 surl（可空，空时使用当前会话已分享）；"
            "pwd 为提取码（可空）。工具只浏览、不转存、不下载；"
            "返回目录树文本，可据此再调用 pan_download_dir/pan_transfer 等工具。"
        ),
        parameters=[
            ToolParameterInfo(
                name="link",
                param_type=ToolParamType.STRING,
                description="百度网盘分享链接或 surl；留空使用当前会话分享",
                required=False,
            ),
            ToolParameterInfo(
                name="pwd",
                param_type=ToolParamType.STRING,
                description="分享提取码；无提取码留空",
                required=False,
            ),
        ],
    )
    async def tool_pan_list(
        self,
        link: str = "",
        pwd: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not self.config.plugin.enabled:
            return {"content": PLUGIN_DISABLED_TEXT}
        identity = extract_identity(kwargs)
        try:
            if identity is not None and self._access().is_blacklisted(identity):
                raise PermissionDeniedError()
            tree = await self._tree_for(link, pwd, kwargs)
            text = render_tree(tree, max_entries=self.config.share.entries_per_dir)
            return {"content": truncate_text(text, 3800)}
        except BaiduPanError as exc:
            return {"content": str(exc)}
        except Exception as exc:  # noqa: BLE001
            self._logger.error("pan_list 异常：%s", exc, exc_info=True)
            return {"content": "读取分享目录失败，请稍后重试或查看插件日志。"}

    @Tool(
        "pan_transfer",
        brief_description="把百度网盘分享中的文件或目录转存到自己的网盘",
        detailed_description=(
            "当用户要求“转存/保存到我的网盘”时调用。"
            "参数：link 为分享链接或 surl（可空，空时使用当前会话已浏览的分享）；"
            "pwd 为提取码（可空）；path 为目录树中的文件/目录路径（可空，空时转存分享根目录全部内容）。"
            "本工具只转存、不下载、不发送文件；需要白名单或管理员权限。"
        ),
        parameters=[
            ToolParameterInfo(
                name="link",
                param_type=ToolParamType.STRING,
                description="百度网盘分享链接或 surl；留空使用当前会话分享",
                required=False,
            ),
            ToolParameterInfo(
                name="pwd",
                param_type=ToolParamType.STRING,
                description="分享提取码；无提取码留空",
                required=False,
            ),
            ToolParameterInfo(
                name="path",
                param_type=ToolParamType.STRING,
                description="目录树中的文件/目录路径；留空表示分享根目录全部内容",
                required=False,
            ),
        ],
    )
    async def tool_pan_transfer(
        self,
        link: str = "",
        pwd: str = "",
        path: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not self.config.plugin.enabled:
            return {"content": PLUGIN_DISABLED_TEXT}
        identity = extract_identity(kwargs)
        try:
            self._access().require_download(identity)
            tree = await self._tree_for(link, pwd, kwargs)
            nodes = [select_node(tree, path)] if path.strip() else root_nodes(tree)
            result = await self._transfer_selected(tree, nodes)
            if result.own_share:
                names = "、".join(Path(item).name for item in result.direct_paths[:5]) or "全部内容"
                return {
                    "content": (
                        f"该分享属于当前登录账号，无需转存；文件已在你的网盘：{names}。"
                        "可以直接在网盘中使用它们。"
                    )
                }
            names = "、".join(Path(name).name for name in result.filenames[:10]) or "全部内容"
            return {"content": f"✅ 已转存到 {result.target_dir}：{names}"}
        except BaiduPanError as exc:
            return {"content": str(exc)}
        except Exception as exc:  # noqa: BLE001 - 工具返回值必须可读
            self._logger.error("pan_transfer 异常：%s", exc, exc_info=True)
            return {"content": "转存失败，请稍后重试或查看插件日志。"}

    @Tool(
        "pan_download_dir",
        brief_description="下载百度网盘分享中的文件或目录并发送到聊天",
        detailed_description=(
            "当用户要求“下载分享里的某个文件/文件夹”时调用。"
            "参数：folder_path 为目录树中的文件或目录路径（必填）；"
            "link 为分享链接或 surl（可空，空时使用当前会话分享）；pwd 为提取码（可空）。"
            "工具会先精确转存，再交给 BaiduPCS-Go 后台下载，完成后把文件发送到当前聊天；"
            "需要白名单或管理员权限，受最大文件大小限制。"
        ),
        parameters=[
            ToolParameterInfo(
                name="folder_path",
                param_type=ToolParamType.STRING,
                description="目录树中的文件或目录路径",
                required=True,
            ),
            ToolParameterInfo(
                name="link",
                param_type=ToolParamType.STRING,
                description="百度网盘分享链接或 surl；留空使用当前会话分享",
                required=False,
            ),
            ToolParameterInfo(
                name="pwd",
                param_type=ToolParamType.STRING,
                description="分享提取码；无提取码留空",
                required=False,
            ),
        ],
    )
    async def tool_pan_download_dir(
        self,
        folder_path: str = "",
        link: str = "",
        pwd: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not self.config.plugin.enabled:
            return {"content": PLUGIN_DISABLED_TEXT}
        identity = extract_identity(kwargs)
        try:
            self._access().require_download(identity)
            if not folder_path.strip():
                return {"content": "请提供目录树中的文件或目录路径。"}
            tree = await self._tree_for(link, pwd, kwargs)
            node = select_node(tree, folder_path)
            size = self._node_total_size(tree, node)
            error = self._size_limit_error(size, "目标")
            if error:
                return {"content": error}
            job_id = self._start_download(tree, [node], kwargs, identity)
            label = "目录" if node.is_dir else "文件"
            return {
                "content": (
                    f"✅ 已受理{label}下载：{node.name}（{format_size(size)}，任务 {job_id}）。"
                    "下载完成后会自动发送到当前聊天。"
                )
            }
        except BaiduPanError as exc:
            return {"content": str(exc)}
        except Exception as exc:  # noqa: BLE001
            self._logger.error("pan_download_dir 异常：%s", exc, exc_info=True)
            return {"content": "下载失败，请稍后重试或查看插件日志。"}

    @Tool(
        "pan_download_all",
        brief_description="下载百度网盘分享中的全部内容并发送到聊天",
        detailed_description=(
            "当用户要求“把整个分享都下载下来”时调用。"
            "参数：link 为分享链接或 surl（可空，空时使用当前会话分享）；pwd 为提取码（可空）。"
            "会转存分享根目录全部内容并后台下载，逐项发送文件；"
            "需要白名单或管理员权限；目标总量超过配置上限时会拒绝。"
        ),
        parameters=[
            ToolParameterInfo(
                name="link",
                param_type=ToolParamType.STRING,
                description="百度网盘分享链接或 surl；留空使用当前会话分享",
                required=False,
            ),
            ToolParameterInfo(
                name="pwd",
                param_type=ToolParamType.STRING,
                description="分享提取码；无提取码留空",
                required=False,
            ),
        ],
    )
    async def tool_pan_download_all(
        self,
        link: str = "",
        pwd: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not self.config.plugin.enabled:
            return {"content": PLUGIN_DISABLED_TEXT}
        identity = extract_identity(kwargs)
        try:
            self._access().require_download(identity)
            tree = await self._tree_for(link, pwd, kwargs)
            nodes = root_nodes(tree)
            if not nodes:
                return {"content": "当前分享根目录没有可下载的内容。"}
            size = sum(self._node_total_size(tree, node) for node in nodes)
            error = self._size_limit_error(size, "分享内容")
            if error:
                return {"content": error}
            job_id = self._start_download(tree, nodes, kwargs, identity, display_name="分享全部内容")
            return {
                "content": (
                    f"✅ 已受理全部内容下载（{len(nodes)} 项，{format_size(size)}，任务 {job_id}）。"
                    "下载完成后会发送到当前聊天。"
                )
            }
        except BaiduPanError as exc:
            return {"content": str(exc)}
        except Exception as exc:  # noqa: BLE001
            self._logger.error("pan_download_all 异常：%s", exc, exc_info=True)
            return {"content": "下载失败，请稍后重试或查看插件日志。"}

    # ------------------------------------------------------------------ BaiduPCS-Go 与登录

    async def _cmd_install(
        self, identity: Identity | None, rest: str, kwargs: dict[str, Any]
    ) -> tuple[bool, str, int]:
        """``/pan install [force|--git]`` 或 ``/pan download``：安装 BaiduPCS-Go。"""

        policy = self._access()
        policy.require_manage(identity)
        tokens = {token.strip().lower() for token in rest.split() if token.strip()}
        force = bool(tokens & {"force", "-f", "--force", "重装"})
        direct_only = bool(tokens & {"git", "--git"})
        path = await self._ensure_pcs(force=force, direct_only=direct_only)
        version = self.config.download.pcs_version
        source = "GitHub 直连" if direct_only else "镜像优先"
        return True, f"BaiduPCS-Go {version} 已就绪（{source}）：{path}", 2

    async def _cmd_login(
        self, identity: Identity | None, rest: str, kwargs: dict[str, Any]
    ) -> tuple[bool, str, int]:
        """``/pan login <账号> <密码>``。"""

        policy = self._access()
        policy.require_manage(identity)
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            return True, "用法：/pan login <百度账号> <密码>（推荐使用 /pan qrlogin 扫码登录）", 2
        client = self._make_pcs_client()
        account = await client.login_password(parts[0], parts[1])
        return True, _login_success_text(account), 2

    async def _cmd_bduss(
        self, identity: Identity | None, rest: str, kwargs: dict[str, Any]
    ) -> tuple[bool, str, int]:
        """``/pan bduss <BDUSS> [STOKEN]``。"""

        policy = self._access()
        policy.require_manage(identity)
        parts = rest.split()
        if not parts:
            return True, "用法：/pan bduss <BDUSS> [STOKEN]", 2
        client = self._make_pcs_client()
        account = await client.login_bduss(parts[0], parts[1] if len(parts) > 1 else "")
        return True, _login_success_text(account), 2

    async def _cmd_cookies(
        self, identity: Identity | None, rest: str, kwargs: dict[str, Any]
    ) -> tuple[bool, str, int]:
        """``/pan cookies <浏览器 Cookie 字符串>``。"""

        policy = self._access()
        policy.require_manage(identity)
        if not rest.strip():
            return True, "用法：/pan cookies <完整 Cookie 字符串>", 2
        client = self._make_pcs_client()
        account = await client.login_cookies(rest.strip())
        return True, _login_success_text(account), 2

    async def _cmd_unlogin(
        self, identity: Identity | None, rest: str, kwargs: dict[str, Any]
    ) -> tuple[bool, str, int]:
        """``/pan unlogin``：退出百度账号。"""

        policy = self._access()
        policy.require_manage(identity)
        client = self._make_pcs_client()
        result = await client.logout()
        if result.code != 0 or result.contains_error_words():
            raise PcsError("退出登录失败，请稍后重试。", detail=result.output)
        self._logger.info("已退出百度账号")
        return True, "已退出百度账号登录。", 2

    async def _cmd_qrlogin(
        self, identity: Identity | None, rest: str, kwargs: dict[str, Any]
    ) -> tuple[bool, str, int]:
        """``/pan qrlogin``：后台获取二维码并等待扫码。"""

        policy = self._access()
        policy.require_manage(identity)
        if self._qr_task is not None and not self._qr_task.done():
            return True, "扫码登录已在进行中，请先完成手机确认。", 2
        stream_id = str(kwargs.get("stream_id") or "")
        self._qr_task = self._spawn(self._qr_login_flow(stream_id), name="qrlogin")
        return True, "正在准备扫码登录，请稍候…", 2

    async def _qr_login_flow(self, stream_id: str) -> None:
        """后台扫码登录流程：发二维码 → 轮询 → 用 Cookie 登录 BaiduPCS-Go。"""

        session = QrLoginSession(logger=self._logger)
        try:
            await self._ensure_pcs()
            qr = await session.create()
            image_base64 = base64.b64encode(qr.image).decode("ascii")
            await self.ctx.send.image(image_base64, stream_id)
            await self._send_text(stream_id, "请使用百度 App 扫描二维码，并在手机上确认登录。二维码约 3 分钟内有效。")
            credentials, _scanned = await session.wait_for_scan(qr.sign)
            client = self._make_pcs_client()
            account = await client.login_cookies(credentials.cookies)
            await self._send_text(stream_id, _login_success_text(account))
            self._logger.info("扫码登录成功：%s", account.name if account else "未知账号")
        except BaiduPanError as exc:
            self._logger.warning("扫码登录失败：%s", exc)
            await self._send_text(stream_id, str(exc))
        except Exception as exc:  # noqa: BLE001 - 后台任务必须自吞异常
            self._logger.error("扫码登录异常：%s", exc, exc_info=True)
            await self._send_text(stream_id, "扫码登录失败，请稍后重试或改用 /pan bduss。")
        finally:
            await session.aclose()


def _login_success_text(account: AccountInfo | None) -> str:
    """生成登录成功提示（不输出任何凭据）。"""

    if account is None or account.uid <= 0:
        return "登录成功，但未能读取账号信息，可使用 /pan status 检查。"
    name = account.name or "未知账号"
    return f"登录成功：{name}（uid {account.uid}）"


def create_plugin() -> BaiduPanPlugin:
    """由 Runner 调用的插件工厂。"""

    return BaiduPanPlugin()


__all__ = ["BaiduPanPlugin", "create_plugin"]
