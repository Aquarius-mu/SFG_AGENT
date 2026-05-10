"""
GBrain-style Minion Queue
确定性任务（$0 · 无 LLM · asyncio）队列，替代 sub-agent 方案。

对比数据（GBrain 实测）：
  Minion:    753ms  $0      100% 成功率
  Sub-agent: 10s+   $0.03   ~60% 成功率
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger("storage.minion")

_DEFAULT_TIMEOUT = 30.0     # 单个任务超时（秒）
_MAX_QUEUE = 200            # 队列容量上限


@dataclass
class MinionTask:
    task_id: str
    handler: Callable[..., Awaitable[Any]]
    kwargs: dict
    created_at: float = field(default_factory=time.monotonic)
    future: asyncio.Future | None = field(default=None, compare=False)


class MinionQueue:
    """
    GBrain Minion：asyncio 确定性任务队列。

    设计原则：
    - 仅接受 is_deterministic=True 的工具
    - 无 LLM 调用，纯 Python/HTTP 执行
    - 并发执行，结果通过 Future 返回
    - 队列满时快速失败（不阻塞 agent loop）
    """

    def __init__(self, concurrency: int = 8, task_timeout: float = _DEFAULT_TIMEOUT):
        self._concurrency = concurrency
        self._task_timeout = task_timeout
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(concurrency)
        self._stats = {"submitted": 0, "completed": 0, "failed": 0}
        self._running = False
        self._worker_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop(), name="minion-worker")
        logger.info("MinionQueue 已启动 (concurrency=%d)", self._concurrency)

    async def stop(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info(
            "MinionQueue 已停止. stats=%s", self._stats
        )

    async def submit(self, handler: Callable, task_id: str = "", **kwargs) -> Any:
        """
        提交确定性任务，返回执行结果。
        快速失败：队列满时立即抛出 RuntimeError。
        worker 未启动时直接执行（不入队，避免双重执行）。
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        task = MinionTask(
            task_id=task_id or f"minion-{self._stats['submitted']}",
            handler=handler,
            kwargs=kwargs,
            future=future,
        )

        self._stats["submitted"] += 1

        # worker 未启动时跳过队列，直接执行，避免任务入队后 start() 时被二次消费
        if not self._running:
            return await self._run_task(task)

        try:
            self._queue.put_nowait(task)
        except asyncio.QueueFull:
            future.set_exception(RuntimeError("Minion 队列已满，请稍后重试"))
            raise

        return await asyncio.wait_for(future, timeout=self._task_timeout)

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                task = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except Exception:
                continue

            asyncio.create_task(self._run_task(task))
            self._queue.task_done()

    async def _run_task(self, task: MinionTask) -> Any:
        async with self._semaphore:
            t0 = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    task.handler(**task.kwargs),
                    timeout=self._task_timeout,
                )
                elapsed = int((time.monotonic() - t0) * 1000)
                logger.debug(
                    "Minion task %s OK (%dms)", task.task_id, elapsed
                )
                self._stats["completed"] += 1
                if task.future and not task.future.done():
                    task.future.set_result(result)
                return result
            except Exception as exc:
                self._stats["failed"] += 1
                logger.warning("Minion task %s FAIL: %s", task.task_id, exc)
                if task.future and not task.future.done():
                    task.future.set_exception(exc)
                raise

    def get_stats(self) -> dict:
        return {
            **self._stats,
            "queue_size": self._queue.qsize(),
            "running": self._running,
        }
