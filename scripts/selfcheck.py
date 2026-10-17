"""插件自检：清单字段、配置模型降级、gitignore 与目录结构。

用法：
    .venv/Scripts/python scripts/selfcheck.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_MANIFEST_FIELDS = (
    "manifest_version",
    "id",
    "version",
    "name",
    "description",
    "author",
    "license",
    "urls",
    "host_application",
    "sdk",
    "capabilities",
    "i18n",
)
REQUIRED_FILES = ("plugin.py", "config.py", "README.md", "_manifest.json", ".gitignore")


def main() -> int:
    errors: list[str] = []
    print("== 百度网盘下载助手 自检 ==")

    manifest_path = ROOT / "_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[FAIL] 读取 _manifest.json 失败：{exc}")
        return 1
    missing = [field for field in REQUIRED_MANIFEST_FIELDS if field not in manifest]
    if missing:
        errors.append(f"manifest 缺少字段：{', '.join(missing)}")
    if manifest.get("manifest_version") != 2:
        errors.append("manifest_version 必须为 2")
    if manifest.get("id") != "blackbee.baidu-pan":
        errors.append("插件 id 与预期不一致")
    print(f"[OK] manifest：{manifest.get('name')} v{manifest.get('version')}")

    for name in REQUIRED_FILES:
        if not (ROOT / name).is_file():
            errors.append(f"缺少必需文件：{name}")
    print(f"[OK] 必需文件：{', '.join(REQUIRED_FILES)}")

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("/config.toml", "/logs/", "/data/"):
        if pattern not in gitignore:
            errors.append(f".gitignore 缺少 {pattern}")
    print("[OK] .gitignore 已覆盖配置/日志/数据目录")

    try:
        from config import BaiduPanConfig  # noqa: E402

        config = BaiduPanConfig()
        assert config.plugin.config_version == "2.0.0"
        assert config.download.pcs_version == "v4.0.2"
        assert "gh-proxy.com" in config.download.pcs_download_prefixes
        assert config.sending.base64_threshold_mb == 8
        print(
            "[OK] 配置默认值："
            f"whitelist={config.access.whitelist_enabled}，"
            f"发送模式={config.sending.mode}，"
            f"清理时刻={config.download.cleanup_hour}:00"
        )
    except Exception as exc:  # noqa: BLE001 - 自检输出友好错误
        errors.append(f"配置模型自检失败（是否已安装 maibot-plugin-sdk？）：{exc}")

    for module in ("baidu_pan/share.py", "baidu_pan/pcs_client.py", "baidu_pan/sender.py", "tests"):
        if not (ROOT / module).exists():
            errors.append(f"缺少模块/目录：{module}")

    if errors:
        print("\n自检失败：")
        for item in errors:
            print(f" - {item}")
        return 1
    print("\n自检通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
