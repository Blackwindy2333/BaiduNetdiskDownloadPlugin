"""下载任务编排：BaiduPCS-Go 下载、进度节流、文件定位、发送与云端清理。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .errors import BaiduPanError, DownloadError
from .models import DownloadJob
from .utils import format_size, now_iso, safe_filename


@dataclass
class DownloadRequest:
    """启动一次下载任务所需的参数。"""

    job_id: str
    stream_id: str
    platform: str
    user_id: str
    display_name: str
    cloud_paths: list[str]
    cleanup_cloud_paths: list[str] = field(default_factory=list)
    share_url: str = ""
    expected_size: int = 0
    expected_names: list[str] = field(default_factory=list)


class DownloadManager:
    """串行/受控并发的下载任务管理器。"""

    def __init__(
        self,
        *,
        config_provider: Callable[[], Any],
        pcs_factory: Callable[[], Any],
        sender: Any,
        notify: Callable[[str, str], Awaitable[None]],
        download_root: Path,
        manifest: Any,
        logger: logging.Logger | None = None,
        max_concurrent: int = 1,
    ) -> None:
        self._config_provider = config_provider
        self._pcs_factory = pcs_factory
        self._sender = sender
        self._notify = notify
        self._download_root = Path(download_root)
        self._manifest = manifest
        self._logger = logger or logging.getLogger(__name__)
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrent)))
        self._tasks: set[asyncio.Task[Any]] = set()
        self._notify_tasks: set[asyncio.Task[Any]] = set()
        self._jobs: dict[str, DownloadJob] = {}

    @property
    def download_root(self) -> Path:
        return self._download_root

    def set_download_root(self, path: Path) -> None:
        """热更新下载根目录（只影响后续任务）。"""

        self._download_root = Path(path)

    def active_jobs(self) -> list[DownloadJob]:
        return [job for job in self._jobs.values() if job.status in {"queued", "running"}]

    def submit(self, request: DownloadRequest) -> DownloadJob:
        job = DownloadJob(
            job_id=request.job_id,
            stream_id=request.stream_id,
            platform=request.platform,
            user_id=request.user_id,
            target=request.display_name,
        )
        self._jobs[job.job_id] = job
        task = asyncio.create_task(self._run(job, request), name=f"download-{job.job_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def shutdown(self) -> None:
        """取消所有任务与后台通知。"""

        for task in list(self._notify_tasks):
            task.cancel()
        self._notify_tasks.clear()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def wait_idle(self) -> None:
        """等待当前所有下载任务结束（测试与关停辅助）。"""

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def drain_notifications(self) -> None:
        """等待已排队的通知发送完成。"""

        if self._notify_tasks:
            await asyncio.gather(*list(self._notify_tasks), return_exceptions=True)

    async def _run(self, job: DownloadJob, request: DownloadRequest) -> None:
        config = self._config_provider()
        async with self._semaphore:
            job.status = "running"
            job.started_at = now_iso()
            self._logger.info(
                "下载任务开始：job=%s target=%s size=%s",
                job.job_id,
                request.display_name,
                format_size(request.expected_size),
            )
            self._schedule_notify(
                job.stream_id,
                f"⏬ 开始下载：{request.display_name}（{format_size(request.expected_size)}）",
            )
            run_dir = self._download_root / job.job_id
            try:
                run_dir.mkdir(parents=True, exist_ok=True)
                pcs = self._pcs_factory()
                timeout = self._resolve_timeout(config, request.expected_size)
                last_progress = [0.0]

                def on_progress(progress: Any) -> None:
                    if not config.download.progress_enabled:
                        return
                    interval = max(5, int(config.download.progress_interval_seconds))
                    now = time.monotonic()
                    if now - last_progress[0] < interval:
                        return
                    last_progress[0] = now
                    text = (
                        f"⏬ {request.display_name}：{progress.percent:.1f}%"
                        f"（{format_size(progress.downloaded)}/{format_size(progress.total)}"
                    )
                    if progress.speed:
                        text += f"，{progress.speed}"
                    text += "）"
                    self._schedule_notify(job.stream_id, text)

                outcome = await pcs.download(
                    request.cloud_paths,
                    run_dir,
                    timeout=timeout,
                    on_progress=on_progress,
                )
                if not outcome.success:
                    reason = "、".join(outcome.failed_lines[:3]) or _last_output_line(outcome.result.output)
                    raise DownloadError(f"下载失败：{reason or '请查看插件日志。'}")

                files = self._locate_files(run_dir, request.expected_names)
                if not files:
                    raise DownloadError("下载完成但未找到文件，请查看插件日志。")
                job.local_paths = [str(path) for path in files]
                job.total_files = len(files)
                try:
                    self._manifest.add(files)
                except Exception as exc:  # noqa: BLE001 - 清单失败不阻断
                    self._logger.warning("登记下载文件失败：%s", exc)

                sent = 0
                failed = 0
                for index, file_path in enumerate(files):
                    if index >= max(1, int(config.sending.max_send_files)):
                        break
                    result = await self._sender.send_file(
                        job.stream_id,
                        file_path,
                        share_url=request.share_url,
                    )
                    if result.success:
                        sent += 1
                    else:
                        failed += 1
                job.finished_files = sent

                if request.cleanup_cloud_paths:
                    for cloud_path in request.cleanup_cloud_paths:
                        try:
                            await pcs.rm(cloud_path)
                        except BaiduPanError as exc:
                            self._logger.warning("清理云端临时文件失败（%s）：%s", cloud_path, exc)

                job.status = "succeeded"
                job.finished_at = now_iso()
                summary = (
                    f"✅ 下载完成：{request.display_name}\n"
                    f"文件数：{len(files)}，已发送：{sent}，发送失败：{failed}\n"
                    f"本地目录：{run_dir}"
                )
                self._schedule_notify(job.stream_id, summary)
                self._logger.info(
                    "下载任务完成：job=%s files=%d sent=%d failed=%d",
                    job.job_id,
                    len(files),
                    sent,
                    failed,
                )
            except asyncio.CancelledError:
                job.status = "cancelled"
                job.message = "任务已取消"
                job.finished_at = now_iso()
                self._logger.info("下载任务已取消：job=%s", job.job_id)
                raise
            except BaiduPanError as exc:
                job.status = "failed"
                job.message = str(exc)
                job.finished_at = now_iso()
                self._logger.warning("下载任务失败：job=%s %s", job.job_id, exc)
                self._schedule_notify(job.stream_id, f"❌ {request.display_name} 处理失败：{exc}")
            except Exception as exc:  # noqa: BLE001 - 任务异常只记录
                job.status = "failed"
                job.message = "任务异常"
                job.finished_at = now_iso()
                self._logger.error("下载任务异常：job=%s %s", job.job_id, exc, exc_info=True)
                self._schedule_notify(job.stream_id, f"❌ {request.display_name} 处理失败，请查看插件日志。")

    @staticmethod
    def _resolve_timeout(config: Any, size: int) -> float:
        configured = int(config.download.timeout_seconds)
        if configured > 0:
            return float(configured)
        if size > 0:
            return float(max(600, int(size / (1024 * 1024) * 15)))
        return 3600.0

    @staticmethod
    def _locate_files(root: Path, expected_names: list[str]) -> list[Path]:
        files: list[Path] = []
        for name in expected_names:
            candidate = root / safe_filename(name)
            if candidate.is_file():
                files.append(candidate)
                continue
            if candidate.is_dir():
                files.extend(sorted(path for path in candidate.rglob("*") if path.is_file()))
        if files:
            return list(dict.fromkeys(files))
        return sorted(path for path in root.rglob("*") if path.is_file())

    def _schedule_notify(self, stream_id: str, text: str) -> None:
        if not stream_id:
            return
        task = asyncio.create_task(self._notify(stream_id, text))
        self._notify_tasks.add(task)
        task.add_done_callback(self._notify_tasks.discard)


def _last_output_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


__all__ = ["DownloadManager", "DownloadRequest"]
