"""下载任务编排：成功链路、失败通知、文件定位与清理。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from baidu_pan.download_manager import DownloadManager, DownloadRequest
from baidu_pan.models import DownloadJob, SendOutcome
from baidu_pan.pcs_client import DownloadOutcome, DownloadProgress, PcsResult
from config import BaiduPanConfig


class FakePcs:
    def __init__(self, files: list[str], success: bool = True) -> None:
        self.files = files
        self.success = success
        self.download_calls: list[dict[str, Any]] = []
        self.rm_calls: list[str] = []

    async def download(self, paths, saveto: Path, *, overwrite=True, timeout, on_progress=None):
        self.download_calls.append(
            {"paths": list(paths), "saveto": Path(saveto), "timeout": timeout, "overwrite": overwrite}
        )
        if self.success:
            for name in self.files:
                target = Path(saveto) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("data", encoding="utf-8")
            if on_progress is not None:
                on_progress(DownloadProgress(downloaded=5, total=10, percent=50.0, speed="1.0MB/s"))
        return DownloadOutcome(
            success=self.success,
            result=PcsResult(
                code=0,
                stdout="下载结束\n" if self.success else "以下文件下载失败:\n/我的资源/a.txt\n",
            ),
            failed_lines=[] if self.success else ["/我的资源/a.txt"],
        )

    async def rm(self, path: str) -> PcsResult:
        self.rm_calls.append(path)
        return PcsResult(code=0, stdout="删除成功")


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[Path] = []

    async def send_file(self, stream_id, path, *, display_name="", share_url=""):
        self.sent.append(Path(path))
        return SendOutcome(True, "base64")


class FakeManifest:
    def __init__(self) -> None:
        self.files: list[Path] = []

    def add(self, paths) -> int:
        self.files.extend(Path(path) for path in paths)
        return len(paths)


def build_manager(
    tmp_path: Path, fake_pcs: FakePcs, *, config: BaiduPanConfig | None = None
):
    sender = FakeSender()
    manifest = FakeManifest()
    notifications: list[tuple[str, str]] = []

    async def notify(stream_id: str, text: str) -> None:
        notifications.append((stream_id, text))

    manager = DownloadManager(
        config_provider=lambda: config or BaiduPanConfig(),
        pcs_factory=lambda: fake_pcs,
        sender=sender,
        notify=notify,
        download_root=tmp_path / "downloads",
        manifest=manifest,
        max_concurrent=1,
    )
    return manager, sender, manifest, notifications


def make_request(**overrides) -> DownloadRequest:
    data: dict[str, Any] = {
        "job_id": "job-1",
        "stream_id": "stream-1",
        "platform": "qq",
        "user_id": "100",
        "display_name": "a.txt",
        "cloud_paths": ["/我的资源/临时/a.txt"],
        "cleanup_cloud_paths": ["/我的资源/临时"],
        "share_url": "https://pan.baidu.com/s/1AbCdEf",
        "expected_size": 5,
        "expected_names": ["a.txt"],
    }
    data.update(overrides)
    return DownloadRequest(**data)


async def test_download_success_chain(tmp_path: Path) -> None:
    fake_pcs = FakePcs(["a.txt"])
    manager, sender, manifest, notifications = build_manager(tmp_path, fake_pcs)

    job = manager.submit(make_request())
    await manager.wait_idle()
    await manager.drain_notifications()

    assert job.status == "succeeded"
    assert sender.sent and sender.sent[0].name == "a.txt"
    assert fake_pcs.rm_calls == ["/我的资源/临时"]
    assert manifest.files and manifest.files[0].name == "a.txt"
    texts = [text for _stream, text in notifications]
    assert any("开始下载" in text for text in texts)
    assert any("下载完成" in text for text in texts)
    assert fake_pcs.download_calls[0]["paths"] == ["/我的资源/临时/a.txt"]
    assert fake_pcs.download_calls[0]["timeout"] >= 600


async def test_download_failure_notifies(tmp_path: Path) -> None:
    fake_pcs = FakePcs([], success=False)
    manager, sender, _manifest, notifications = build_manager(tmp_path, fake_pcs)

    job = manager.submit(make_request())
    await manager.wait_idle()
    await manager.drain_notifications()

    assert job.status == "failed"
    assert sender.sent == []
    assert any("处理失败" in text for text in (item[1] for item in notifications))


async def test_download_respects_max_send_files(tmp_path: Path) -> None:
    config = BaiduPanConfig(sending={"max_send_files": 1})
    fake_pcs = FakePcs(["a.txt", "b.txt"])
    manager, sender, _manifest, _notifications = build_manager(tmp_path, fake_pcs, config=config)

    job = manager.submit(make_request(expected_names=["a.txt", "b.txt"]))
    await manager.wait_idle()
    await manager.drain_notifications()

    assert job.status == "succeeded"
    assert len(sender.sent) == 1


def test_locate_files_directory(tmp_path: Path) -> None:
    directory = tmp_path / "downloads" / "job" / "文件夹"
    directory.mkdir(parents=True)
    (directory / "a.txt").write_text("a", encoding="utf-8")
    (directory / "b.txt").write_text("b", encoding="utf-8")
    files = DownloadManager._locate_files(tmp_path / "downloads" / "job", ["文件夹"])
    assert [path.name for path in files] == ["a.txt", "b.txt"]


def test_resolve_timeout() -> None:
    assert DownloadManager._resolve_timeout(
        BaiduPanConfig(download={"timeout_seconds": 60}), 0
    ) == 60.0
    auto = DownloadManager._resolve_timeout(
        BaiduPanConfig(download={"timeout_seconds": 0}), 100 * 1024 * 1024
    )
    assert auto == 1500.0
    assert DownloadManager._resolve_timeout(
        BaiduPanConfig(download={"timeout_seconds": 0}), 0
    ) == 3600.0
    assert DownloadManager._resolve_timeout(
        BaiduPanConfig(download={"timeout_seconds": 0}), 10, True
    ) == 3600.0


async def test_partial_files_registered_on_failure(tmp_path: Path) -> None:
    class PartialPcs(FakePcs):
        async def download(self, paths, saveto: Path, *, overwrite=True, timeout, on_progress=None):
            partial = Path(saveto) / "partial.bin"
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_text("x", encoding="utf-8")
            return DownloadOutcome(False, PcsResult(code=0, stdout="下载失败"), ["失败"])

    manager, _sender, manifest, _notifications = build_manager(tmp_path, PartialPcs([]))
    job = manager.submit(make_request())
    await manager.wait_idle()
    await manager.drain_notifications()
    assert job.status == "failed"
    assert manifest.files and manifest.files[0].name == "partial.bin"


async def test_manager_shutdown_cancels_task(tmp_path: Path) -> None:
    manager, _sender, _manifest, _notifications = build_manager(tmp_path, FakePcs([]))

    async def slow_download(*args, **kwargs):
        import asyncio

        await asyncio.sleep(10)
        return DownloadOutcome(True, PcsResult(code=0))

    manager._pcs_factory = lambda: type("SlowPcs", (), {"download": slow_download, "rm": lambda self, path: None})()
    job = manager.submit(make_request())
    import asyncio

    await asyncio.sleep(0.05)
    await manager.shutdown()
    assert job.status == "cancelled"
