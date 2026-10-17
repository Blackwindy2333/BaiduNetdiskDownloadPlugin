"""文件发送：传输选择、载荷形态与失败回退。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from baidu_pan.sender import FileSender
from config import BaiduPanConfig


class FakeSendProxy:
    def __init__(self) -> None:
        self.custom_calls: list[tuple[tuple, dict]] = []
        self.text_calls: list[tuple[str, str]] = []
        self.custom_result: Any = {"success": True, "sent": True, "message_id": "m1"}

    async def custom(self, *args, **kwargs):
        self.custom_calls.append((args, kwargs))
        return self.custom_result

    async def text(self, text: str, stream_id: str, **kwargs):
        self.text_calls.append((text, stream_id))
        return True


def make_sender(tmp_path: Path, **sending) -> tuple[FileSender, FakeSendProxy, list[Path]]:
    proxy = FakeSendProxy()
    config = BaiduPanConfig(sending=sending)
    leftover: list[Path] = []
    sender = FileSender(
        proxy,
        config_provider=lambda: config,
        staging_dir_provider=lambda: tmp_path / "staging",
        on_leftover=lambda path: leftover.append(path),
    )
    return sender, proxy, leftover


async def test_send_small_file_as_base64(tmp_path: Path) -> None:
    target = tmp_path / "hello.txt"
    target.write_text("hello", encoding="utf-8")
    sender, proxy, _ = make_sender(tmp_path, mode="auto")

    outcome = await sender.send_file("stream", target, display_name="你好.txt")
    assert outcome.success is True
    assert outcome.transport == "base64"
    args, kwargs = proxy.custom_calls[0]
    assert args[0] == "file"
    assert args[1]["name"] == "你好.txt"
    assert args[1]["url"].startswith("base64://")
    assert args[1]["size"] == "5"
    assert kwargs["return_details"] is True


async def test_send_local_path_copies_and_cleans(tmp_path: Path) -> None:
    target = tmp_path / "big.bin"
    target.write_bytes(b"x" * 2048)
    sender, proxy, _ = make_sender(tmp_path, mode="local")

    outcome = await sender.send_file("stream", target)
    assert outcome.success is True
    assert outcome.transport == "local"
    args, _ = proxy.custom_calls[0]
    assert args[1]["url"].startswith("file:///")
    # 暂存副本发送成功后应被删除
    assert list((tmp_path / "staging").glob("upload-*")) == []


async def test_send_failure_falls_back_to_link(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")
    sender, proxy, _ = make_sender(tmp_path, mode="auto", link_fallback=True)
    proxy.custom_result = {"success": False}

    outcome = await sender.send_file(
        "stream", target, share_url="https://pan.baidu.com/s/1AbCdEf?pwd=052x"
    )
    assert outcome.success is False
    assert outcome.fallback_used is True
    assert proxy.text_calls
    assert "https://pan.baidu.com/s/1AbCdEf" in proxy.text_calls[0][0]


async def test_send_local_failure_keeps_staging(tmp_path: Path) -> None:
    target = tmp_path / "big.bin"
    target.write_bytes(b"x" * 16)
    sender, proxy, leftover = make_sender(tmp_path, mode="local", link_fallback=False)
    proxy.custom_result = None

    outcome = await sender.send_file("stream", target)
    assert outcome.success is False
    assert outcome.leftover_path
    assert leftover
    assert list((tmp_path / "staging").glob("upload-*"))


async def test_link_mode_does_not_send_file(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")
    sender, proxy, _ = make_sender(tmp_path, mode="link")

    outcome = await sender.send_file("stream", target, share_url="https://pan.baidu.com/s/1AbCdEf")
    assert outcome.success is False
    assert proxy.custom_calls == []
    assert proxy.text_calls


async def test_missing_file(tmp_path: Path) -> None:
    sender, proxy, _ = make_sender(tmp_path)
    outcome = await sender.send_file("stream", tmp_path / "missing.txt")
    assert outcome.success is False
    assert "读取文件失败" in outcome.detail
    assert proxy.custom_calls == []


async def test_base64_threshold_overflow_falls_back(tmp_path: Path) -> None:
    target = tmp_path / "big.bin"
    target.write_bytes(b"x" * (2 * 1024 * 1024))
    sender, proxy, _ = make_sender(tmp_path, mode="base64", base64_threshold_mb=1)
    outcome = await sender.send_file("stream", target, share_url="https://pan.baidu.com/s/1AbCdEf")
    assert outcome.success is False
    assert proxy.custom_calls == []
    assert proxy.text_calls
