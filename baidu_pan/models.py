"""领域模型：分享信息、目录树、下载任务与发送结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .utils import format_size, now_iso

DownloadStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


@dataclass(slots=True)
class ShareInfo:
    """一个已解析的百度网盘分享。"""

    surl: str
    share_id: str
    uk: str
    pwd: str = ""
    bdstoken: str = ""
    title: str = ""

    @property
    def key(self) -> str:
        return f"{self.share_id}:{self.uk}"


@dataclass(slots=True)
class ShareNode:
    """分享目录树中的一个文件或目录。"""

    name: str
    path: str
    is_dir: bool
    size: int = 0
    fs_id: int | str | None = None

    @property
    def display_size(self) -> str:
        return "-" if self.is_dir else format_size(self.size)

    @property
    def basename(self) -> str:
        return self.name or self.path.rstrip("/").rsplit("/", 1)[-1]


@dataclass(slots=True)
class ShareTree:
    """一次分享浏览的结果。"""

    info: ShareInfo
    nodes: list[ShareNode] = field(default_factory=list)
    root_name: str = ""
    truncated: bool = False
    built_at: str = field(default_factory=now_iso)

    def find(self, path: str) -> ShareNode | None:
        normalized = path.strip().strip("/")
        for node in self.nodes:
            if node.path.strip().strip("/") == normalized:
                return node
        return None

    def find_by_basename(self, name: str) -> list[ShareNode]:
        target = name.strip().strip("/")
        return [node for node in self.nodes if node.basename == target]

    def files(self) -> list[ShareNode]:
        return [node for node in self.nodes if not node.is_dir]

    def total_bytes(self) -> int:
        return sum(node.size for node in self.nodes if not node.is_dir)


@dataclass(slots=True)
class DownloadJob:
    """一次下载任务的运行状态。"""

    job_id: str
    stream_id: str
    platform: str
    user_id: str
    target: str
    status: DownloadStatus = "queued"
    total_files: int = 0
    finished_files: int = 0
    local_paths: list[str] = field(default_factory=list)
    message: str = ""
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "stream_id": self.stream_id,
            "platform": self.platform,
            "user_id": self.user_id,
            "target": self.target,
            "status": self.status,
            "total_files": self.total_files,
            "finished_files": self.finished_files,
            "local_paths": list(self.local_paths),
            "message": self.message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass(slots=True)
class SendOutcome:
    """一次文件/文本发送的结果（用于日志与回退判断）。"""

    success: bool
    transport: str
    detail: str = ""
    fallback_used: bool = False
    leftover_path: str = ""
    sent_at: str = field(default_factory=now_iso)


__all__ = [
    "DownloadJob",
    "DownloadStatus",
    "SendOutcome",
    "ShareInfo",
    "ShareNode",
    "ShareTree",
]
