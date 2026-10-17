"""插件配置模型。

每个配置节对应 ``config.toml`` 中的一个 ``[section]``。Runner 会依据本模型
在首次加载时生成 ``config.toml``、在版本演进时补齐缺失字段，WebUI 则依据
``__ui_*`` 元数据渲染分组与字段说明。
"""

from __future__ import annotations

from typing import ClassVar, Literal

from maibot_sdk.config import PluginConfigBase
from pydantic import BaseModel, ConfigDict, Field, field_validator

_SECTION_CONFIG = ConfigDict(validate_assignment=True, extra="ignore")

_DEFAULT_CLOUD_DIR = "/我的资源/AutoTransfer"


def _normalize_multi_value(value: object) -> str:
    """把逗号/分号/空白/全角逗号分隔的字符串规范化为英文逗号分隔。"""

    if value is None:
        return ""
    text = str(value)
    for separator in ("，", "；", ";", "\n", "\r", "\t", " "):
        text = text.replace(separator, ",")
    items = [item.strip() for item in text.split(",") if item.strip()]
    return ",".join(dict.fromkeys(items))


def _normalize_account_list(value: object) -> str:
    """规范化账号列表：逗号分隔，去掉 ``@`` 前缀并去重。"""

    text = _normalize_multi_value(value)
    items = [item.lstrip("@").strip() for item in text.split(",") if item.strip()]
    return ",".join(dict.fromkeys(items))


def _check_no_parent_traversal(value: str, field_name: str) -> str:
    """拒绝 NUL 与 ``..`` 路径段，避免越权写入。"""

    text = value.strip()
    if "\x00" in text:
        raise ValueError(f"{field_name} 不能包含空字符")
    normalized = text.replace("\\", "/")
    if ".." in normalized.split("/"):
        raise ValueError(f"{field_name} 不能包含 .. 路径段")
    return text


class PluginInfoSection(BaseModel):
    """插件开关。"""

    __ui_label__: ClassVar[str] = "插件"
    __ui_icon__: ClassVar[str] = "package"
    __ui_order__: ClassVar[int] = 0

    model_config = _SECTION_CONFIG

    enabled: bool = Field(default=True, description="是否启用百度网盘下载助手")
    config_version: str = Field(default="2.0.0", description="配置结构版本，由插件维护，请勿手动修改")


class AccessSection(BaseModel):
    """管理员、白名单与黑名单。"""

    __ui_label__: ClassVar[str] = "权限与白名单"
    __ui_icon__: ClassVar[str] = "shield-check"
    __ui_order__: ClassVar[int] = 10

    model_config = _SECTION_CONFIG

    admin_users: str = Field(
        default="",
        description="管理员账号，逗号分隔；支持 qq:123456 或裸数字 123456。留空表示尚未配置管理员",
        json_schema_extra={"placeholder": "qq:123456,qq:234567"},
    )
    whitelist_enabled: bool = Field(
        default=True,
        description="是否只允许管理员和白名单用户发起下载/转存请求",
    )
    admin_only_sensitive: bool = Field(
        default=True,
        description="登录、安装、卸载、状态与白名单管理等敏感命令是否仅管理员可用",
    )
    blacklist: str = Field(
        default="",
        description="黑名单账号，逗号分隔；命中后命令静默、工具提示无权限",
        json_schema_extra={"placeholder": "qq:123456"},
    )

    @field_validator("admin_users", "blacklist", mode="before")
    @classmethod
    def _normalize_account_lists(cls, value: object) -> str:
        return _normalize_account_list(value)


class ShareSection(BaseModel):
    """分享链接浏览会话。"""

    __ui_label__: ClassVar[str] = "分享浏览"
    __ui_icon__: ClassVar[str] = "folder-tree"
    __ui_order__: ClassVar[int] = 20

    model_config = _SECTION_CONFIG

    cache_ttl_minutes: int = Field(default=30, ge=1, le=1440, description="分享会话缓存有效期（分钟）")
    max_depth: int = Field(default=3, ge=1, le=6, description="目录树最大递归深度")
    max_items: int = Field(default=300, ge=20, le=2000, description="单次目录树最多节点数")
    entries_per_dir: int = Field(default=60, ge=10, le=500, description="每个目录最多展示的条目数")


class TransferSection(BaseModel):
    """分享文件转存。"""

    __ui_label__: ClassVar[str] = "转存"
    __ui_icon__: ClassVar[str] = "copy"
    __ui_order__: ClassVar[int] = 30

    model_config = _SECTION_CONFIG

    cloud_save_dir: str = Field(
        default=_DEFAULT_CLOUD_DIR,
        description="转存到网盘的目标目录（绝对路径）",
        json_schema_extra={"placeholder": _DEFAULT_CLOUD_DIR},
    )
    auto_delete_cloud: bool = Field(default=True, description="发送/下载成功后删除网盘中的临时转存文件")
    max_file_size_mb: int = Field(default=0, ge=0, le=1048576, description="单文件大小上限（MB），0 表示不限制")
    verify_after_transfer: bool = Field(default=True, description="转存后复查目标文件是否存在")

    @field_validator("cloud_save_dir", mode="before")
    @classmethod
    def _normalize_cloud_dir(cls, value: object) -> str:
        text = _check_no_parent_traversal(str(value or _DEFAULT_CLOUD_DIR), "transfer.cloud_save_dir")
        if not text:
            text = _DEFAULT_CLOUD_DIR
        if not text.startswith("/"):
            text = "/" + text
        return text.rstrip("/") or "/"


class DownloadSection(BaseModel):
    """BaiduPCS-Go 安装与下载任务。"""

    __ui_label__: ClassVar[str] = "下载与 BaiduPCS-Go"
    __ui_icon__: ClassVar[str] = "download"
    __ui_order__: ClassVar[int] = 40

    model_config = _SECTION_CONFIG

    baidu_pcs_go_path: str = Field(
        default="",
        description="BaiduPCS-Go 可执行文件路径；留空使用 data 目录下自动安装的版本",
        json_schema_extra={"placeholder": r"D:\Tools\BaiduPCS-Go.exe"},
    )
    pcs_version: str = Field(default="v4.0.2", description="自动安装的 BaiduPCS-Go 版本（内置 v4.0.2 校验哈希）")
    pcs_download_prefixes: str = Field(
        default="https://gh-proxy.com/,https://git.yylx.win/",
        description="GitHub 加速镜像前缀，逗号分隔；按顺序尝试后回退 GitHub 直连",
        json_schema_extra={"placeholder": "https://gh-proxy.com/,https://git.yylx.win/"},
    )
    pcs_verify_checksum: bool = Field(default=True, description="是否校验 BaiduPCS-Go 的大小与 SHA256")
    download_dir: str = Field(
        default="",
        description="下载文件保存目录；留空使用 data 目录下 downloads",
        json_schema_extra={"placeholder": r"D:\Downloads\BaiduPan"},
    )
    max_concurrent_jobs: int = Field(default=1, ge=1, le=3, description="同时运行的下载任务数")
    timeout_seconds: int = Field(default=0, ge=0, le=86400, description="下载超时（秒），0 表示按文件大小自动计算")
    progress_enabled: bool = Field(default=False, description="是否在聊天中发送下载进度")
    progress_interval_seconds: int = Field(default=30, ge=5, le=600, description="进度消息最小间隔（秒）")
    cleanup_hour: int = Field(default=3, ge=-1, le=23, description="每日清理下载文件的时刻（0-23），-1 表示关闭")
    retention_hours: int = Field(default=24, ge=1, le=720, description="下载文件保留时长（小时）")

    @field_validator("baidu_pcs_go_path", "download_dir", mode="before")
    @classmethod
    def _validate_optional_paths(cls, value: object) -> str:
        return _check_no_parent_traversal(str(value or ""), "download 路径")

    @field_validator("pcs_download_prefixes", mode="before")
    @classmethod
    def _normalize_mirrors(cls, value: object) -> str:
        text = str(value or "").replace("，", ",")
        prefixes = [item.strip() for item in text.split(",") if item.strip()]
        normalized: list[str] = []
        for prefix in prefixes:
            if not prefix.startswith(("http://", "https://")):
                raise ValueError("pcs_download_prefixes 中的镜像必须以 http:// 或 https:// 开头")
            if not prefix.endswith("/"):
                prefix += "/"
            normalized.append(prefix)
        return ",".join(dict.fromkeys(normalized))


class SendingSection(BaseModel):
    """文件发送策略。"""

    __ui_label__: ClassVar[str] = "文件发送"
    __ui_icon__: ClassVar[str] = "send"
    __ui_order__: ClassVar[int] = 50

    model_config = _SECTION_CONFIG

    mode: Literal["auto", "base64", "local", "link"] = Field(
        default="auto",
        description="发送方式：auto=小文件 base64/大文件共享路径/失败回退链接",
    )
    base64_threshold_mb: int = Field(default=8, ge=1, le=10, description="Base64 发送的大小阈值（MB），Host IPC 硬上限为 10 MB")
    staging_dir: str = Field(
        default="",
        description="大文件共享暂存目录，需 NapCat/SnowLuma 与 MaiBot 同机可读；留空使用 data 目录下 upload_cache",
        json_schema_extra={"placeholder": r"D:\MaiBotShared\baidu-pan"},
    )
    local_path_enabled: bool = Field(default=True, description="是否允许通过 file:/// 共享路径发送大文件")
    link_fallback: bool = Field(default=True, description="发送失败时是否回退发送分享链接与提取码")
    max_send_files: int = Field(default=10, ge=1, le=50, description="单个任务最多发送的文件数，超出改为列出本地路径")

    @field_validator("staging_dir", mode="before")
    @classmethod
    def _validate_staging_dir(cls, value: object) -> str:
        return _check_no_parent_traversal(str(value or ""), "sending.staging_dir")


class AccountSection(BaseModel):
    """可选启动自动登录凭据。"""

    __ui_label__: ClassVar[str] = "账号"
    __ui_icon__: ClassVar[str] = "user"
    __ui_order__: ClassVar[int] = 60

    model_config = _SECTION_CONFIG

    bduss: str = Field(default="", description="可选：BDUSS，用于启动自动登录", json_schema_extra={"secret": True})
    stoken: str = Field(default="", description="可选：STOKEN", json_schema_extra={"secret": True})
    auto_login: bool = Field(default=False, description="启动时使用上述凭据自动登录 BaiduPCS-Go")

    @field_validator("bduss", "stoken", mode="before")
    @classmethod
    def _strip_credentials(cls, value: object) -> str:
        return str(value or "").strip()


class LoggingSection(BaseModel):
    """日志策略。"""

    __ui_label__: ClassVar[str] = "日志"
    __ui_icon__: ClassVar[str] = "scroll-text"
    __ui_order__: ClassVar[int] = 70

    model_config = _SECTION_CONFIG

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO", description="日志级别")
    file_enabled: bool = Field(default=True, description="是否写入 data 目录下的轮转日志文件")
    file_max_bytes: int = Field(default=2097152, ge=65536, le=104857600, description="单个日志文件大小上限（字节）")
    file_backup_count: int = Field(default=3, ge=0, le=20, description="日志轮转保留数")


class BaiduPanConfig(PluginConfigBase):
    """插件配置根模型：每个字段对应 ``config.toml`` 的一个 ``[section]``。"""

    __ui_label__: ClassVar[str] = "百度网盘下载助手"
    __ui_icon__: ClassVar[str] = "download"
    __ui_order__: ClassVar[int] = 0

    plugin: PluginInfoSection = Field(default_factory=PluginInfoSection, description="插件开关")
    access: AccessSection = Field(default_factory=AccessSection, description="权限与白名单")
    share: ShareSection = Field(default_factory=ShareSection, description="分享浏览")
    transfer: TransferSection = Field(default_factory=TransferSection, description="转存")
    download: DownloadSection = Field(default_factory=DownloadSection, description="下载与 BaiduPCS-Go")
    sending: SendingSection = Field(default_factory=SendingSection, description="文件发送")
    account: AccountSection = Field(default_factory=AccountSection, description="账号")
    logging: LoggingSection = Field(default_factory=LoggingSection, description="日志")


__all__ = [
    "AccessSection",
    "AccountSection",
    "BaiduPanConfig",
    "DownloadSection",
    "LoggingSection",
    "PluginInfoSection",
    "SendingSection",
    "ShareSection",
    "TransferSection",
]
