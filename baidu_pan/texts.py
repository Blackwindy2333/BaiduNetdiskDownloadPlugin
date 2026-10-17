"""面向用户的简体中文文案。

集中管理便于后续做文案调整或 i18n；命令处理器只引用这里的常量/函数。
"""

from __future__ import annotations

from .__version__ import PLUGIN_NAME, PLUGIN_VERSION

PLUGIN_DISABLED_TEXT = "百度网盘下载助手已停用，请联系管理员在插件后台开启。"
UNKNOWN_COMMAND_TEXT = "未知的 /pan 子命令。发送 /pan help 查看完整帮助。"
NO_SHARE_SESSION_TEXT = "当前对话还没有缓存的分享。请先发送 /pan <分享链接> [提取码]。"
TASK_ACCEPTED_TEXT = "已受理任务，稍后会发送结果；可用 /pan status 查看运行状态。"
INVALID_TARGET_TEXT = "请提供有效的 QQ 号（例如 123456），或直接 @ 一位成员。"
WHITELIST_EMPTY_TEXT = "白名单为空。"


def whitelist_added_text(account: str, count: int) -> str:
    return f"✅ 已加入白名单：{account}（当前共 {count} 人）"


def whitelist_exists_text(account: str) -> str:
    return f"该用户已在白名单中：{account}"


def whitelist_removed_text(account: str, count: int) -> str:
    return f"🗑️ 已从白名单移除：{account}（当前共 {count} 人）"


def whitelist_missing_text(account: str) -> str:
    return f"白名单中没有该用户：{account}"


def whitelist_list_text(accounts: list[str]) -> str:
    if not accounts:
        return WHITELIST_EMPTY_TEXT
    lines = [f"📋 白名单（{len(accounts)} 人）："]
    lines.extend(f"{index}. {account}" for index, account in enumerate(accounts, start=1))
    return "\n".join(lines)


def status_text(
    *,
    admin_count: int,
    whitelist_count: int,
    whitelist_enabled: bool,
    sending_mode: str,
    pcs_version: str,
) -> str:
    return "\n".join(
        [
            "📊 百度网盘下载助手运行状态",
            f"· 管理员：{admin_count} 人",
            f"· 白名单：{whitelist_count} 人（{'启用' if whitelist_enabled else '关闭'}）",
            f"· 发送模式：{sending_mode}",
            f"· BaiduPCS-Go：{pcs_version}",
        ]
    )


def build_help_text() -> str:
    """生成帮助页。"""

    return f"""📦 {PLUGIN_NAME} v{PLUGIN_VERSION}

【浏览分享】
/pan <链接或surl> [提取码] 解析并浏览分享根目录
/pan ls [链接] [提取码] 刷新分享目录
/pan look 查看当前缓存的目录树

【下载】
/pan file <路径> 下载单个文件并发送
/pan dir <路径> 下载整个目录并发送
/pan install [force|--git] 安装/更新 BaiduPCS-Go
（--git 强制 GitHub 直连；别名 /pan download）

【账号】（仅管理员）
/pan qrlogin 扫码登录（推荐）
/pan bduss <BDUSS> [STOKEN]
/pan cookies <cookie>
/pan login <账号> <密码>
/pan unlogin 退出登录
/pan status 查看登录与任务状态

【白名单】（仅管理员）
/pan add <QQ号或@成员> 添加白名单
/pan del <QQ号或@成员> 删除白名单
/pan wlist 列出白名单用户

【说明】
· 下载/转存需要管理员或白名单权限；浏览对普通成员开放。
· 单文件大小上限、下载目录、发送方式等由后台配置决定。
· 大文件通过共享目录发送，失败时回退为分享链接。
· 详细文档见插件 README 与 docs/ADAPTER.md。"""


__all__ = [
    "INVALID_TARGET_TEXT",
    "NO_SHARE_SESSION_TEXT",
    "PLUGIN_DISABLED_TEXT",
    "TASK_ACCEPTED_TEXT",
    "UNKNOWN_COMMAND_TEXT",
    "WHITELIST_EMPTY_TEXT",
    "build_help_text",
    "status_text",
    "whitelist_added_text",
    "whitelist_exists_text",
    "whitelist_list_text",
    "whitelist_missing_text",
    "whitelist_removed_text",
]
