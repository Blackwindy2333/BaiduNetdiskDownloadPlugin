"""配置模型：默认值、规范化与校验。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config import BaiduPanConfig


def test_defaults() -> None:
    config = BaiduPanConfig()
    assert config.plugin.enabled is True
    assert config.plugin.config_version == "2.0.0"
    assert config.access.admin_users == ""
    assert config.access.whitelist_enabled is True
    assert config.access.admin_only_sensitive is True
    assert config.share.cache_ttl_minutes == 30
    assert config.share.max_depth == 3
    assert config.transfer.cloud_save_dir == "/我的资源/AutoTransfer"
    assert config.transfer.max_file_size_mb == 0
    assert config.download.pcs_version == "v4.0.2"
    assert config.download.pcs_download_prefixes == "https://gh-proxy.com/,https://git.yylx.win/"
    assert config.download.max_concurrent_jobs == 1
    assert config.sending.mode == "auto"
    assert config.sending.base64_threshold_mb == 8
    assert config.logging.level == "INFO"
    assert config.logging.file_enabled is True


def test_admin_and_blacklist_normalization() -> None:
    config = BaiduPanConfig(
        access={
            "admin_users": " qq:123, 456；@789 , qq:123 ",
            "blacklist": "qq:999；888",
        }
    )
    assert config.access.admin_users == "qq:123,456,789"
    assert config.access.blacklist == "qq:999,888"


def test_cloud_dir_normalization() -> None:
    config = BaiduPanConfig(transfer={"cloud_save_dir": "我的资源/临时目录/"})
    assert config.transfer.cloud_save_dir == "/我的资源/临时目录"

    root = BaiduPanConfig(transfer={"cloud_save_dir": "/"})
    assert root.transfer.cloud_save_dir == "/"


def test_cloud_dir_rejects_parent_traversal() -> None:
    with pytest.raises(ValidationError):
        BaiduPanConfig(transfer={"cloud_save_dir": "/我的资源/../etc"})


def test_mirror_normalization() -> None:
    config = BaiduPanConfig(
        download={"pcs_download_prefixes": "https://gh-proxy.com, https://git.yylx.win/"}
    )
    assert config.download.pcs_download_prefixes == (
        "https://gh-proxy.com/,https://git.yylx.win/"
    )


def test_mirror_must_be_http() -> None:
    with pytest.raises(ValidationError):
        BaiduPanConfig(download={"pcs_download_prefixes": "ftp://example.com/"})


def test_sending_threshold_bounds() -> None:
    with pytest.raises(ValidationError):
        BaiduPanConfig(sending={"base64_threshold_mb": 11})
    with pytest.raises(ValidationError):
        BaiduPanConfig(sending={"base64_threshold_mb": 0})


def test_sending_mode_validation() -> None:
    with pytest.raises(ValidationError):
        BaiduPanConfig(sending={"mode": "unknown"})
    config = BaiduPanConfig(sending={"mode": "local"})
    assert config.sending.mode == "local"


def test_path_traversal_rejected() -> None:
    with pytest.raises(ValidationError):
        BaiduPanConfig(download={"download_dir": r"D:\downloads\..\Windows"})
    with pytest.raises(ValidationError):
        BaiduPanConfig(sending={"staging_dir": "../shared"})


def test_range_validation() -> None:
    with pytest.raises(ValidationError):
        BaiduPanConfig(download={"cleanup_hour": 24})
    with pytest.raises(ValidationError):
        BaiduPanConfig(share={"max_depth": 0})
    with pytest.raises(ValidationError):
        BaiduPanConfig(logging={"level": "TRACE"})


def test_config_is_serializable() -> None:
    config = BaiduPanConfig()
    data = config.model_dump()
    assert set(data) == {
        "plugin",
        "access",
        "share",
        "transfer",
        "download",
        "sending",
        "account",
        "logging",
    }
    assert data["plugin"]["config_version"] == "2.0.0"


def test_assign_validation() -> None:
    config = BaiduPanConfig()
    with pytest.raises(ValidationError):
        config.share.max_depth = 99
