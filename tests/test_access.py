"""权限、白名单与身份解析。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from baidu_pan.access import (
    AccessPolicy,
    Identity,
    WhitelistStore,
    extract_at_target,
    extract_identity,
    parse_target_identity,
)
from baidu_pan.errors import AdminNotConfiguredError, PermissionDeniedError
from config import BaiduPanConfig


def make_config(**sections):
    return BaiduPanConfig(**sections)


@pytest.fixture()
def store(tmp_path: Path) -> WhitelistStore:
    return WhitelistStore(tmp_path / "state" / "whitelist.json")


def test_extract_identity_from_flat_kwargs() -> None:
    identity = extract_identity({"platform": "qq", "user_id": "123456"})
    assert identity == Identity(platform="qq", user_id="123456")


def test_extract_identity_from_nested_message() -> None:
    identity = extract_identity(
        {
            "platform": "onebot",
            "message": {
                "message_info": {"user_info": {"user_id": "777"}},
            },
        }
    )
    assert identity is not None
    assert identity.platform == "qq"
    assert identity.user_id == "777"


def test_extract_identity_missing_returns_none() -> None:
    assert extract_identity({}) is None
    assert extract_identity({"message": {"message_info": {}}}) is None


def test_extract_at_target() -> None:
    kwargs = {
        "message": {
            "message_segments": [
                {"type": "text", "data": {"text": "/pan add "}},
                {"type": "at", "data": {"qq": "246810"}},
            ]
        }
    }
    identity = extract_at_target(kwargs)
    assert identity == Identity(platform="qq", user_id="246810")


def test_parse_target_prefers_at_segment() -> None:
    kwargs = {
        "message": {
            "message_segments": [{"type": "at", "data": {"qq": "246810"}}],
        }
    }
    assert parse_target_identity(kwargs, "") == Identity(platform="qq", user_id="246810")
    assert parse_target_identity({}, "13579") == Identity(platform="qq", user_id="13579")
    assert parse_target_identity({}, "qq:13579") == Identity(platform="qq", user_id="13579")
    assert parse_target_identity({}, "") is None


def test_whitelist_persist_and_reload(store: WhitelistStore, tmp_path: Path) -> None:
    identity = Identity(platform="qq", user_id="111")
    assert store.add(identity, added_by="qq:1") is True
    assert store.add(identity) is False
    assert store.count() == 1
    assert store.contains(identity) is True

    reloaded = WhitelistStore(tmp_path / "state" / "whitelist.json")
    assert reloaded.contains(identity) is True
    assert reloaded.list_users()[0]["added_by"] == "qq:1"

    assert reloaded.remove(identity) is True
    assert reloaded.remove(identity) is False
    assert reloaded.count() == 0


def test_whitelist_corrupt_file_is_backed_up(tmp_path: Path) -> None:
    path = tmp_path / "state" / "whitelist.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")

    store = WhitelistStore(path)
    assert store.count() == 0
    backups = list(path.parent.glob("whitelist.json.corrupt-*.bak"))
    assert backups
    assert json.loads(path.read_text(encoding="utf-8"))["users"] == []


def test_policy_admin_can_download(store: WhitelistStore) -> None:
    config = make_config(access={"admin_users": "qq:100"})
    policy = AccessPolicy(config, store)
    assert policy.admin_configured() is True
    assert policy.is_admin(Identity("qq", "100")) is True
    assert policy.can_download(Identity("qq", "100")) is True
    policy.require_download(Identity("qq", "100"))


def test_policy_no_admin_blocks_everyone(store: WhitelistStore) -> None:
    config = make_config(access={"admin_users": ""})
    policy = AccessPolicy(config, store)
    assert policy.admin_configured() is False
    assert policy.can_download(Identity("qq", "100")) is False
    with pytest.raises(AdminNotConfiguredError):
        policy.require_download(Identity("qq", "100"))


def test_policy_whitelist_gate(store: WhitelistStore) -> None:
    config = make_config(access={"admin_users": "qq:100", "whitelist_enabled": True})
    policy = AccessPolicy(config, store)
    member = Identity("qq", "200")
    with pytest.raises(PermissionDeniedError):
        policy.require_download(member)

    store.add(member)
    assert policy.can_download(member) is True
    policy.require_download(member)


def test_policy_whitelist_disabled_allows_all(store: WhitelistStore) -> None:
    config = make_config(access={"admin_users": "qq:100", "whitelist_enabled": False})
    policy = AccessPolicy(config, store)
    assert policy.can_download(Identity("qq", "999")) is True


def test_policy_blacklist_overrides_admin(store: WhitelistStore) -> None:
    config = make_config(access={"admin_users": "qq:100", "blacklist": "qq:100"})
    policy = AccessPolicy(config, store)
    assert policy.is_blacklisted(Identity("qq", "100")) is True
    assert policy.can_download(Identity("qq", "100")) is False
    with pytest.raises(PermissionDeniedError):
        policy.require_admin(Identity("qq", "100"))


def test_policy_platform_alias(store: WhitelistStore) -> None:
    config = make_config(access={"admin_users": "onebot:100"})
    policy = AccessPolicy(config, store)
    assert policy.is_admin(Identity("qq", "100")) is True


def test_policy_sensitive_requires_admin_by_default(store: WhitelistStore) -> None:
    config = make_config(access={"admin_users": "qq:100", "whitelist_enabled": True})
    policy = AccessPolicy(config, store)
    member = Identity("qq", "200")
    store.add(member)
    with pytest.raises(PermissionDeniedError):
        policy.require_manage(member)

    open_config = make_config(
        access={"admin_users": "qq:100", "whitelist_enabled": True, "admin_only_sensitive": False}
    )
    open_policy = AccessPolicy(open_config, store)
    open_policy.require_manage(member)


def test_whitelist_file_path_under_state(store: WhitelistStore, tmp_path: Path) -> None:
    assert store.path == tmp_path / "state" / "whitelist.json"
    assert store.path.exists()
