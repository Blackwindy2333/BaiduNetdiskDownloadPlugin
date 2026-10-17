"""本地下载文件清单与定时清理。

只清理「插件自己下载/暂存」的文件，避免误删下载目录中的其他内容。
清单写入 ``data_dir/state/cleanup_manifest.json``，采用原子写。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .utils import atomic_write_json, now_iso, read_json


@dataclass
class CleanupReport:
    deleted: int = 0
    missing: int = 0
    errors: int = 0
    remaining: int = 0
    deleted_paths: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"清理完成：删除 {self.deleted} 个文件，失效 {self.missing} 条，失败 {self.errors} 个"


class FileManifest:
    """记录插件下载/暂存的文件，供定时清理。"""

    def __init__(self, path: Path, logger: logging.Logger | None = None) -> None:
        self._path = Path(path)
        self._logger = logger or logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._entries: list[dict[str, object]] = []
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        payload = read_json(self._path, {})
        entries: list[dict[str, object]] = []
        raw = payload.get("files") if isinstance(payload, dict) else payload
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    entries.append({"path": item, "created_at": "", "size": 0})
                elif isinstance(item, dict) and item.get("path"):
                    entries.append(
                        {
                            "path": str(item.get("path")),
                            "created_at": str(item.get("created_at") or ""),
                            "size": int(item.get("size") or 0),
                        }
                    )
        with self._lock:
            self._entries = entries

    def _persist_locked(self) -> None:
        atomic_write_json(
            self._path,
            {"version": 1, "updated_at": now_iso(), "files": self._entries},
        )

    def add(self, paths: list[Path]) -> int:
        """登记文件；已存在的路径不会重复记录。"""

        added = 0
        with self._lock:
            known = {str(item["path"]) for item in self._entries}
            for path in paths:
                text = str(Path(path))
                if text in known:
                    continue
                try:
                    size = Path(path).stat().st_size
                except OSError:
                    size = 0
                self._entries.append({"path": text, "created_at": now_iso(), "size": size})
                known.add(text)
                added += 1
            if added:
                self._persist_locked()
        return added

    def entries(self) -> list[dict[str, object]]:
        with self._lock:
            return [dict(item) for item in self._entries]

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def cleanup(self, allowed_roots: list[Path], *, retention_hours: int) -> CleanupReport:
        """删除清单内、位于允许根目录下且超过保留期的文件。"""

        report = CleanupReport()
        cutoff = time.time() - max(1, retention_hours) * 3600
        roots = [Path(root).resolve() for root in allowed_roots]
        remaining: list[dict[str, object]] = []
        with self._lock:
            for item in self._entries:
                path = Path(str(item.get("path") or ""))
                if not path.is_file():
                    report.missing += 1
                    continue
                try:
                    resolved = path.resolve()
                    if not any(_is_within(resolved, root) for root in roots):
                        remaining.append(item)
                        continue
                    mtime = resolved.stat().st_mtime
                    if mtime > cutoff:
                        remaining.append(item)
                        continue
                    resolved.unlink()
                    report.deleted += 1
                    report.deleted_paths.append(str(resolved))
                except OSError as exc:
                    report.errors += 1
                    self._logger.warning("清理文件失败（%s）：%s", path, exc)
                    remaining.append(item)
            self._entries = remaining
            report.remaining = len(remaining)
            self._persist_locked()
        return report


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


__all__ = ["CleanupReport", "FileManifest"]
