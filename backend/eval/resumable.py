"""**逐题落盘 + 断点续跑** —— 评测脚本共用。

## 为什么抽成共用模块（2026-06-16）

G3 那套先写了一份（`g3_per_option_eval.py` 的 `_load_done` / 逐题 jsonl），
实测救过一次命：跑 254 道跑到一半被中断，重跑时**只补缺口**，没白花一遍额度。

而 `marking_eval`（批改一致性，**3 倍开销**）与 `run_eval`（三路线对照）**没有**这套机制 ——
一旦中途 Ctrl+C，全部作废。三处各写一份必然分叉（本项目已经因为这个栽过几次），
所以这里先收敛成一份，新脚本用这个，G3 那份后续逐步并过来。

## 契约（刻意做得很小）

- **一律 jsonl**：一行一条记录，追加写。中断不会产生半行（写完立刻 flush）。
- **key 由调用方给**：不同评测的"同一道题"含义不同（G3 是题目 id，
  批改一致性是 `题目 id + 第几轮`），所以 `key_of` 是参数而不是固定字段。
- **不做去重语义**：重复 key 以**首次**为准（先落盘的赢），与 G3 一致 ——
  重跑被中断的那一轮时，已完成的不会被覆盖。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Callable, Iterable, Sequence

JsonlPath = str | pathlib.Path


def load_done(path: JsonlPath, key_of: Callable[[dict], str] | None = None) -> dict[str, dict]:
    """读已落盘的记录 → `{key: 记录}`。文件不存在 / 空行损坏都返回空字典。

    损坏行**跳过而不抛**：一条坏记录不该让整批续跑失败（那等于没有续跑）。
    """
    p = pathlib.Path(path)
    out: dict[str, dict] = {}
    if not p.exists():
        return out
    key_fn = key_of or (lambda r: str(r.get("id")))
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # 半行/坏行：跳过，不拖垮整批
        if isinstance(rec, dict):
            out.setdefault(key_fn(rec), rec)
    return out


def append_record(path: JsonlPath, rec: dict) -> None:
    """追加一条记录（写完立刻 flush —— 进程被杀也不丢这一条）。"""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()


def todo_keys(items: Sequence[Any], done: dict[str, dict],
              key_of: Callable[[Any], str]) -> list[Any]:
    """还没跑过的条目（**保序**：输出顺序与输入一致，便于对照）。

    ⚠️ 条目可以是**任何类型**，不只是 dict —— 由调用方的 `key_of` 决定"怎么算它的键"。
    之所以放宽（2026-06-16）：`faithfulness_eval` 的条目是**问句字符串**（`list[str]`），
    而原先的签名只收 dict。**类型签名比实现更窄，就是在邀请别人绕过共用件**：
    那份实现本来就是 `key_of(it) not in done`，与条目是不是 dict 毫无关系 ——
    而它当场就导致旁边手写了一份同形的过滤（正是 `resumable.py` 开头要消掉的那种分叉）。
    """
    return [it for it in items if key_of(it) not in done]


def summarise(done: dict[str, dict], pending: Iterable[dict]) -> str:
    """给命令行用的一句进度说明：已完成 N 条，本次还要跑 M 条。"""
    n_pending = len(list(pending))
    if not done:
        return f"本次跑 {n_pending} 条（无历史记录）"
    return f"↻ 断点续跑：已有 {len(done)} 条结果，本次补 {n_pending} 条"


def result_path(results_dir: JsonlPath, prefix: str, tag: str) -> pathlib.Path:
    """结果文件名约定：`<前缀>_<tag>.jsonl`。tag 必填 —— 没有 tag 就无法区分两次运行。"""
    return pathlib.Path(results_dir) / f"{prefix}_{tag or 'default'}.jsonl"


def dump(obj: Any) -> str:
    """json 序列化（评测记录里可能有 numpy 之外的东西，这里只保证中文可读）。"""
    return json.dumps(obj, ensure_ascii=False)
