"""BaiduPCS-Go 客户端：输出解析、命令拼装与超时。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from baidu_pan.errors import PcsError, PcsTimeoutError
from baidu_pan.pcs_client import PcsClient, PcsResult


def make_client(tmp_path: Path) -> PcsClient:
    executable = tmp_path / "fake-pcs"
    executable.write_bytes(b"stub")
    return PcsClient(executable, config_dir=tmp_path / "pcs-config")


async def test_who_parses_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(tmp_path)

    async def fake_run(args, **kwargs):
        return PcsResult(code=0, stdout="当前帐号 uid: 42, 用户名: 张三, 性别: 男, 年龄: 3.0\n")

    monkeypatch.setattr(client, "run", fake_run)
    account = await client.who()
    assert account is not None
    assert account.uid == 42
    assert account.name == "张三"


async def test_who_logged_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(tmp_path)

    async def fake_run(args, **kwargs):
        return PcsResult(code=0, stdout="当前帐号 uid: 0, 用户名: , 性别: , 年龄: 0.0\n")

    monkeypatch.setattr(client, "run", fake_run)
    assert await client.who() is None


async def test_login_bduss_success_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(tmp_path)
    calls: list[list[str]] = []

    async def fake_run(args, **kwargs):
        calls.append(args)
        if args[0] == "login":
            return PcsResult(code=0, stdout="百度帐号登录成功: 张三\n")
        return PcsResult(code=0, stdout="当前帐号 uid: 7, 用户名: 张三\n")

    monkeypatch.setattr(client, "run", fake_run)
    account = await client.login_bduss("bduss-value", "stoken-value")
    assert account is not None and account.uid == 7
    assert calls[0][0] == "login"
    assert any(arg.startswith("-bduss=") for arg in calls[0])
    assert any(arg.startswith("-stoken=") for arg in calls[0])

    async def failing_run(args, **kwargs):
        return PcsResult(code=0, stdout="登录失败: 密码错误\n")

    monkeypatch.setattr(client, "run", failing_run)
    with pytest.raises(PcsError):
        await client.login_bduss("bad")


async def test_ls_parses_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(tmp_path)
    output = (
        " 0 123456 250528 2.00KB 2024-01-01 12:00 2024-01-02 13:30 "
        "abcdef0123456789abcdef0123456789 文件.txt\n"
        " 1 223456 250528 - 2024-01-01 12:00 2024-01-01 12:00 "
        "abcdef0123456789abcdef0123456789 目录/\n"
        "总: 2.00KB\n"
    )

    async def fake_run(args, **kwargs):
        return PcsResult(code=0, stdout=output)

    monkeypatch.setattr(client, "run", fake_run)
    entries = await client.ls("/我的资源")
    assert len(entries) == 2
    assert entries[0].name == "文件.txt"
    assert entries[0].is_dir is False
    assert entries[0].size == 2048
    assert entries[1].name == "目录"
    assert entries[1].is_dir is True


async def test_download_builds_args_and_detects_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_client(tmp_path)
    calls: list[list[str]] = []

    async def fake_run(args, **kwargs):
        calls.append(args)
        return PcsResult(code=0, stdout="下载结束, 时间: 1s, 数据总量: 2MB\n")

    monkeypatch.setattr(client, "run", fake_run)
    outcome = await client.download(["/我的资源/a.txt"], tmp_path / "downloads", timeout=60)
    assert outcome.success is True
    assert calls[0][:4] == ["download", "--ow", "--saveto", str(tmp_path / "downloads")]
    assert calls[0][-1] == "/我的资源/a.txt"

    async def failing_run(args, **kwargs):
        return PcsResult(code=0, stdout="下载结束\n以下文件下载失败: \n/我的资源/a.txt\n")

    monkeypatch.setattr(client, "run", failing_run)
    outcome = await client.download(["/我的资源/a.txt"], tmp_path / "downloads", timeout=60)
    assert outcome.success is False
    assert outcome.failed_lines


async def test_run_rejects_empty_args(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    with pytest.raises(PcsError):
        await client.run([])


async def test_run_missing_executable(tmp_path: Path) -> None:
    client = PcsClient(tmp_path / "missing")
    with pytest.raises(PcsError):
        await client.run(["who"])


async def test_run_timeout_kills_process(tmp_path: Path) -> None:
    client = PcsClient(Path(sys.executable), timeout=10)
    with pytest.raises(PcsTimeoutError):
        await client.run(["-c", "import time; time.sleep(5)"], timeout=0.2)
