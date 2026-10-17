"""BaiduPCS-Go 本地配置读取（账号凭据复用）。

插件不自行保存 BDUSS：扫码/密码登录统一写入 BaiduPCS-Go 自己的
``pcs_config.json``，浏览分享时再从该文件读取 Cookie，保证两处状态一致。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .errors import AuthError
from .utils import read_json

CONFIG_DIR_ENV = "BAIDUPCS_GO_CONFIG_DIR"
CONFIG_FILE_NAME = "pcs_config.json"


def pcs_config_candidates(exe_path: str = "") -> list[Path]:
    """按 BaiduPCS-Go 的查找顺序列出可能的配置文件路径。"""

    candidates: list[Path] = []
    env_dir = os.environ.get(CONFIG_DIR_ENV, "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser() / CONFIG_FILE_NAME)
    if exe_path:
        candidates.append(Path(exe_path).expanduser().parent / CONFIG_FILE_NAME)

    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        candidates.append(Path(appdata) / "BaiduPCS-Go" / CONFIG_FILE_NAME)
    candidates.append(Path.home() / ".config" / "BaiduPCS-Go" / CONFIG_FILE_NAME)

    return list(dict.fromkeys(candidates))


def find_pcs_config(exe_path: str = "") -> Path | None:
    for candidate in pcs_config_candidates(exe_path):
        if candidate.is_file():
            return candidate
    return None


def load_pcs_config(exe_path: str = "") -> tuple[Path | None, dict[str, Any]]:
    """读取 BaiduPCS-Go 配置；不存在时返回 ``(None, {})``。"""

    path = find_pcs_config(exe_path)
    if path is None:
        return None, {}
    data = read_json(path, {})
    return path, data if isinstance(data, dict) else {}


def _active_user(data: dict[str, Any]) -> dict[str, Any]:
    users = data.get("baidu_user_list")
    if not isinstance(users, list) or not users:
        return {}
    active_uid = str(data.get("baidu_active_uid") or "")
    for user in users:
        if not isinstance(user, dict):
            continue
        if active_uid and str(user.get("uid") or "") == active_uid:
            return user
    return users[0] if isinstance(users[0], dict) else {}


def sanitize_cookie_header(cookies: str) -> str:
    """清理 Cookie 串：去掉无值/BFESS 段，保留有效键值对。"""

    parts: list[str] = []
    for segment in str(cookies or "").split(";"):
        item = segment.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not value or "_BFESS" in name.upper():
            continue
        parts.append(f"{name}={value}")
    return "; ".join(parts)


def cookie_header_from_config(data: dict[str, Any]) -> str:
    """从 BaiduPCS-Go 配置对象提取可用于 pan.baidu.com 的 Cookie。"""

    user = _active_user(data)
    if not user:
        return ""
    cookies = sanitize_cookie_header(str(user.get("cookies") or ""))
    if cookies:
        return cookies
    bduss = str(user.get("bduss") or "").strip()
    if not bduss:
        return ""
    header = f"BDUSS={bduss}"
    stoken = str(user.get("stoken") or "").strip()
    if stoken:
        header += f"; STOKEN={stoken}"
    return header


def load_cookie_header(exe_path: str = "") -> str:
    """读取当前登录 Cookie；未登录时抛出 ``AuthError``。"""

    path, data = load_pcs_config(exe_path)
    cookies = cookie_header_from_config(data)
    if not path:
        raise AuthError("尚未找到 BaiduPCS-Go 配置，请先使用 /pan qrlogin 登录。")
    if not cookies:
        raise AuthError("BaiduPCS-Go 尚未登录，请先使用 /pan qrlogin 登录。")
    return cookies


__all__ = [
    "CONFIG_DIR_ENV",
    "CONFIG_FILE_NAME",
    "cookie_header_from_config",
    "find_pcs_config",
    "load_cookie_header",
    "load_pcs_config",
    "pcs_config_candidates",
    "sanitize_cookie_header",
]
