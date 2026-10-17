"""统一的业务异常类型。

所有面向用户的错误都应抛出这里的子类；插件入口负责把异常转成简体中文
提示，避免把堆栈或第三方响应原文直接暴露给聊天用户。
"""

from __future__ import annotations


class BaiduPanError(Exception):
    """插件业务异常基类。"""

    default_message = "操作失败，请稍后重试"

    def __init__(self, message: str = "", *, code: str = "", detail: str = "") -> None:
        self.message = (message or self.default_message).strip()
        self.code = code
        self.detail = detail
        super().__init__(self.message)

    def __str__(self) -> str:  # pragma: no cover - 简单委托
        return self.message


class PermissionDeniedError(BaiduPanError):
    default_message = "你没有权限使用该功能。"


class AdminNotConfiguredError(BaiduPanError):
    default_message = "尚未配置管理员账号，请先在插件后台配置 access.admin_users。"


class BusyError(BaiduPanError):
    default_message = "当前有任务正在进行，请稍后再试。"


class AuthError(BaiduPanError):
    default_message = "百度账号未登录或登录已过期，请先使用 /pan qrlogin 登录。"


class ShareParseError(BaiduPanError):
    default_message = "分享链接解析失败，请检查链接是否有效。"


class PasswordRequiredError(BaiduPanError):
    default_message = "该分享需要提取码，请使用 /pan <链接> <提取码> 重试。"


class PasswordInvalidError(BaiduPanError):
    default_message = "提取码错误，请确认后重试。"


class ShareNotFoundError(BaiduPanError):
    default_message = "分享不存在或已失效。"


class TargetNotFoundError(BaiduPanError):
    default_message = "未在当前分享中找到该路径，请先用 /pan look 查看目录树。"


class TransferError(BaiduPanError):
    default_message = "转存失败，请稍后重试。"


class InstallError(BaiduPanError):
    default_message = "BaiduPCS-Go 安装失败，请检查网络或手动配置 download.baidu_pcs_go_path。"


class PcsError(BaiduPanError):
    default_message = "BaiduPCS-Go 执行失败，请查看日志获取详细信息。"


class PcsTimeoutError(PcsError):
    default_message = "BaiduPCS-Go 命令执行超时，请稍后重试。"


class QrLoginError(BaiduPanError):
    default_message = "扫码登录失败，请重新执行 /pan qrlogin。"


class DownloadError(BaiduPanError):
    default_message = "下载失败，请稍后重试。"


class SendError(BaiduPanError):
    default_message = "文件发送失败。"


__all__ = [
    "AdminNotConfiguredError",
    "AuthError",
    "BaiduPanError",
    "BusyError",
    "DownloadError",
    "InstallError",
    "PasswordInvalidError",
    "PasswordRequiredError",
    "PcsError",
    "PcsTimeoutError",
    "PermissionDeniedError",
    "QrLoginError",
    "SendError",
    "ShareNotFoundError",
    "ShareParseError",
    "TargetNotFoundError",
    "TransferError",
]
