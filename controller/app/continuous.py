"""Continuous scan manager for orchestrating repeating scans."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .database import SessionLocal
from .schemas import ContinuousScanRequest, ScanRequest, ScanType
from .services import enqueue_scan, run_scan_job

logger = logging.getLogger(__name__)


@dataclass
class ContinuousScanTask:
    agent_id: int
    scan_type: ScanType
    interface: str
    interval_seconds: int
    channels: Optional[list[int]] = None
    extras: Optional[dict] = field(default_factory=dict)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None


class ContinuousScanManager:
    def __init__(self) -> None:
        self._tasks: Dict[Tuple[int, str, str], ContinuousScanTask] = {}
        self._lock = asyncio.Lock()

    def _key(self, agent_id: int, interface: str, scan_type: ScanType) -> Tuple[int, str, str]:
        return (agent_id, interface, scan_type.value)

    async def start(self, request: ContinuousScanRequest) -> None:
        key = self._key(request.agent_id, request.interface, request.scan_type)
        async with self._lock:
            await self._stop_internal(key)
            task_info = ContinuousScanTask(
                agent_id=request.agent_id,
                scan_type=request.scan_type,
                interface=request.interface,
                interval_seconds=request.interval_seconds,
                channels=request.channels,
                extras=request.extras or {},
            )
            loop_task = asyncio.create_task(self._run_loop(task_info))
            task_info.task = loop_task
            self._tasks[key] = task_info
            logger.info(
                "Started continuous scan agent=%s interface=%s type=%s interval=%ss",
                request.agent_id,
                request.interface,
                request.scan_type,
                request.interval_seconds,
            )

    async def stop(self, agent_id: int, interface: str, scan_type: ScanType) -> bool:
        key = self._key(agent_id, interface, scan_type)
        async with self._lock:
            return await self._stop_internal(key)

    async def _stop_internal(self, key: Tuple[int, str, str]) -> bool:
        task_info = self._tasks.pop(key, None)
        if not task_info:
            return False
        task_info.stop_event.set()
        if task_info.task:
            task_info.task.cancel()
            try:
                await task_info.task
            except asyncio.CancelledError:
                pass
        logger.info(
            "Stopped continuous scan agent=%s interface=%s type=%s",
            task_info.agent_id,
            task_info.interface,
            task_info.scan_type,
        )
        return True

    async def shutdown(self) -> None:
        async with self._lock:
            keys = list(self._tasks.keys())
        for key in keys:
            await self._stop_internal(key)

    def list(self) -> list[dict]:
        return [
            {
                "agent_id": task.agent_id,
                "interface": task.interface,
                "scan_type": task.scan_type.value,
                "interval_seconds": task.interval_seconds,
            }
            for task in self._tasks.values()
        ]

    async def _run_loop(self, task_info: ContinuousScanTask) -> None:
        while not task_info.stop_event.is_set():
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._run_single_scan, task_info
                )
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception(
                    "Continuous scan failed agent=%s interface=%s type=%s: %s",
                    task_info.agent_id,
                    task_info.interface,
                    task_info.scan_type,
                    exc,
                )
            try:
                await asyncio.wait_for(
                    task_info.stop_event.wait(), timeout=task_info.interval_seconds
                )
            except asyncio.TimeoutError:
                continue
        logger.info(
            "Continuous scan loop exiting agent=%s interface=%s type=%s",
            task_info.agent_id,
            task_info.interface,
            task_info.scan_type,
        )

    def _run_single_scan(self, task_info: ContinuousScanTask) -> None:
        with SessionLocal() as session:
            scan_req = ScanRequest(
                agent_id=task_info.agent_id,
                scan_type=task_info.scan_type,
                interface=task_info.interface,
                channels=task_info.channels,
                extras=task_info.extras,
            )
            job = enqueue_scan(session, scan_req)
            session.flush()
            job_id = job.id
        if job_id is not None:
            run_scan_job(job_id)


continuous_manager = ContinuousScanManager()
