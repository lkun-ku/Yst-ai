"""出题 / 向量化后台任务的并发池（#26 P2）。

背景：此前每个任务直接 `threading.Thread(...).start()` 裸起线程——
- 并发无上限：多用户同时出题会同时打出 N 倍 LLM 请求，触发供应商限流（429）
  并把单次延迟进一步拉高；
- 无法观测在途任务数，也无法排队与拒绝。

统一收敛为**有界线程池**，超出并发的任务排队而非同时打出去。
标准库 `concurrent.futures`，不引入新依赖。

**为什么出题(gen)与向量化(embed)必须分池**：
出题任务在 worker 内会调用 `wait_embed_ready` 等待切片向量化完成；若两者共用
一个池，embed 任务可能全部排在队尾无法执行，而出题 worker 又在等它 → 死锁。
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

from ..config import settings

GEN = "gen"
EMBED = "embed"

_pools: dict[str, ThreadPoolExecutor] = {}


def get_pool(name: str = GEN) -> ThreadPoolExecutor:
    """按用途取线程池（惰性创建）。"""
    pool = _pools.get(name)
    if pool is None:
        workers = settings.doc_embed_workers if name == EMBED else settings.doc_gen_workers
        pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix=name)
        _pools[name] = pool
    return pool


def submit(fn, *args, pool: str = GEN, **kwargs) -> Future:
    """提交后台任务；超出并发上限时排队，不会新建线程。"""
    return get_pool(pool).submit(fn, *args, **kwargs)


def in_flight(name: str = GEN) -> int:
    """粗略在途任务数（排队 + 执行中）。仅用于可观测，失败返回 -1。"""
    try:
        return int(get_pool(name)._work_queue.qsize())  # noqa: SLF001
    except Exception:
        return -1
