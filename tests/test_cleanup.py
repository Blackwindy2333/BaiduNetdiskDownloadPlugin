"""本地文件清单与定时清理。"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from baidu_pan.cleanup import (
    CleanupScheduler,
    FileManifest,
    seconds_until_hour,
)


def make_manifest(tmp_path: Path) -> FileManifest:
    return FileManifest(tmp_path / "state" / "cleanup_manifest.json")


def touch_old(path: Path, age_hours: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    old = time.time() - age_hours * 3600
    os.utime(path, (old, old))


def test_manifest_add_persist_and_reload(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path)
    file_path = tmp_path / "downloads" / "a.txt"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("hello", encoding="utf-8")

    assert manifest.add([file_path]) == 1
    assert manifest.add([file_path]) == 0
    assert manifest.count() == 1

    reloaded = FileManifest(tmp_path / "state" / "cleanup_manifest.json")
    assert reloaded.count() == 1
    assert reloaded.entries()[0]["path"] == str(file_path)


def test_cleanup_only_deletes_old_files_in_allowed_roots(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path)
    download_root = tmp_path / "downloads"
    staging_root = tmp_path / "staging"

    old_file = download_root / "old.txt"
    touch_old(old_file, 48)
    new_file = download_root / "new.txt"
    touch_old(new_file, 1)
    outside_file = tmp_path / "outside.txt"
    touch_old(outside_file, 48)
    missing_file = download_root / "missing.txt"

    manifest.add([old_file, new_file, outside_file, missing_file])
    report = manifest.cleanup([download_root, staging_root], retention_hours=24)

    assert report.deleted == 1
    assert not old_file.exists()
    assert new_file.exists()
    assert outside_file.exists()
    assert report.missing == 1
    assert report.remaining == 2


def test_cleanup_does_not_follow_outside_roots(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path)
    root = tmp_path / "downloads"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    touch_old(outside, 48)
    manifest.add([outside])
    report = manifest.cleanup([root], retention_hours=1)
    assert report.deleted == 0
    assert outside.exists()


def test_seconds_until_hour() -> None:
    now = datetime(2026, 1, 1, 10, 30, 0)
    assert seconds_until_hour(3, now=now) == 16 * 3600 + 30 * 60
    assert seconds_until_hour(12, now=now) == 1 * 3600 + 30 * 60
    midnight = datetime(2026, 1, 1, 3, 0, 0)
    assert seconds_until_hour(3, now=midnight) == 24 * 3600


def test_scheduler_disabled_when_hour_negative(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path)
    scheduler = CleanupScheduler(
        manifest=manifest,
        roots_provider=lambda: [tmp_path],
        hour_provider=lambda: -1,
        retention_provider=lambda: 24,
    )
    import asyncio

    async def run() -> None:
        await scheduler.start()
        assert scheduler.running is False
        await scheduler.stop()

    asyncio.run(run())


def test_scheduler_run_once(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path)
    root = tmp_path / "downloads"
    old_file = root / "old.txt"
    touch_old(old_file, 48)
    manifest.add([old_file])
    scheduler = CleanupScheduler(
        manifest=manifest,
        roots_provider=lambda: [root],
        hour_provider=lambda: 3,
        retention_provider=lambda: 1,
    )
    report = scheduler.run_once()
    assert report.deleted == 1
    assert not old_file.exists()
