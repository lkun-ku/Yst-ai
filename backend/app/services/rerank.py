"""精排（rerank）：两阶段检索的**第二阶段**。

## 为什么需要它

第一阶段（向量 ⊕ 关键词 → RRF）只做**召回**，排序依据是「词形重合 + 向量相似」，
它看不见 Query 与候选之间的**联合语义**。收益上限是可以量化的：
在法条域（`eval/datasets/官方法条/`，n=135，编号类查询）上
稀疏通道 `recall@1 = 0.7111`、`recall@5 = 0.9778` ——

> **前 5 个里几乎一定有正确答案，但排在第 1 位的不一定是它。**

剩下那 0.27 的空间只能靠"把对的往前挪"来拿，这正是精排的职责。
（上限即 `recall@pool − recall@1`，所以精排做不出超过召回率的效果 ——
召回没捞进来的东西，精排无从排序。）

## 为什么是 Cross-Encoder，而不是再叠一层向量

Cross-Encoder 把 query 与 passage **拼成一条序列**送进模型，两侧在每一层互相注意；
双塔（各自编码后算余弦）没有这层交互，信息量不是一个量级。
代价是**不能预计算** —— 每个 `(query, passage)` 组合都要过一次模型，
所以只能用在几十条的候选池上，且顺序必须是「先召回、再精排」。

## 三种实现，按可用性降级（`get_reranker()`）

| `RERANK_IMPL` | 实现 | 何时用 |
| --- | --- | --- |
| `onnx`（默认） | `OnnxReranker` | 生产 / 评测：权重在 `data/models/bge-reranker-base/` |
| `fake` | `FakeReranker` | 需要走一遍重排链路但零成本（pipeline 测试） |
| `off` | 不重排 | 测试套件默认（**不加载 266MB 模型**） |

**fail-open 是刻意的**：精排是**增强**不是**依赖** —— 权重没下、ONNX 加载失败、
分词器不兼容，都不该让检索整体失败，而应退回「不重排」并把状态暴露在
`status()` 里，而不是静默假装重排过了（那会让"精排有效"这件事永远无法证伪）。

**测试为何默认 `off`**：真实模型要么让测试变慢、要么让测试依赖 266MB 下载。
所以测试套件走 `RERANK_IMPL=off`，需要验证链路时显式用 `fake`；
**真实模型的收益由 `eval/retrieval_eval.py --rerank` 测量**，不在单测里。

## 权重

不随仓库走（266MB）。下载：

    python -m app.services.rerank --fetch
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol, Sequence

from ..config import settings

logger = logging.getLogger(__name__)

#: 模型文件约定（与 `--fetch` 落盘的名字一致）
MODEL_FILE = "model_int8.onnx"
TOKENIZER_FILE = "tokenizer.json"

#: 每批前向的候选数：CPU 上一次 16 条与一次 1 条的总耗时差别不大，
#: 但批量能少付十几次 Python↔ONNX 的往返开销。
BATCH_SIZE = 16

#: HuggingFace 上的权重来源（int8 档 266MB；fp32 是 1.06GB，对演示机负担过大）。
REMOTE_REPO = "Xenova/bge-reranker-base"
REMOTE_FILES = (
    ("onnx/model_int8.onnx", MODEL_FILE),
    ("tokenizer.json", TOKENIZER_FILE),
    ("tokenizer_config.json", "tokenizer_config.json"),
    ("config.json", "config.json"),
)


def _passage_text(chunk: dict, include_heading: bool, max_chars: int) -> str:
    """候选切片 → 送进模型的一段文本。

    `include_heading` 默认 **True**，由实测决定（法条域 n=135，编号类查询）：

    | 喂什么 | recall@1 | MRR |
    | --- | --- | --- |
    | 含标题 | **0.8000** | **0.8753** |
    | 只喂正文 | 0.5630 | 0.7181 |
    | （完全不精排） | 0.7111 | 0.8227 |

    **只喂正文比不精排还差** —— 这很反直觉，但原因清楚：法名与条号只出现在
    `heading_path` 里（正文里没有），只喂正文时模型分不清
    「教师法第七条」与「未成年人保护法第七条」，于是会把语义更像的那条排上来。

    ⚠️ 同一件事的另一面：含标题的增益**主要来自元数据**，不是 Cross-Encoder 的语义理解。
    所以评测里两种口径并排报（`eval/retrieval_eval.py --rerank`），不合并成一个数字。
    """
    parts: list[str] = []
    if include_heading and chunk.get("heading_path"):
        parts.append(str(chunk["heading_path"]))
    parts.append(str(chunk.get("content") or ""))
    return "\n".join(p for p in parts if p)[:max_chars]


def _bigrams(text: str) -> list[str]:
    """与检索层同源的二元组切分（fake 实现用；真实实现走模型自己的分词器）。"""
    t = "".join(ch for ch in str(text or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    if len(t) < 2:
        return [t] if t else []
    return [t[i : i + 2] for i in range(len(t) - 1)]


class Reranker(Protocol):
    """精排器的契约（Ports & Adapters 里的 Port）。

    实现必须满足：`available()` 为真时 `rerank()` 才能改变顺序；
    `available()` 为假时调用方**不应调用** `rerank()`（调用方按不重排处理）。
    """

    name: str

    def available(self) -> bool: ...

    def status(self) -> str: ...

    def rerank(self, query: str, candidates: Sequence[dict], top_k: int) -> list[dict]: ...


class FakeReranker:
    """离线确定性的**替身**：按查询与候选二元组集合的 **Jaccard** 相似度排序。

    **它不是模型，也不代表精排能力** —— 存在的意义只有两个：
    ① 让「重排」这段链路在零成本、无网络、无权重的条件下**可被测试**；
    ② 让 `RERANK_IMPL` 的三种取值在测试里可表达。

    **为什么用 Jaccard 而不是"命中比例"**：后者与稀疏通道的命中数**排序完全相同**
    （分子一样、分母是常数），于是把 `FakeReranker` 接上生产后顺序一点不变 ——
    测试**测不出"重排这段链路到底接上没有"**。Jaccard 多了一个"候选有多长"的维度，
    排序会真的与粗排不同，这才让链路测试有意义。

    它的依据仍然是词形重合，**不能**用它论证"精排有效"——
    那个结论只能来自真实模型（见模块 docstring）。
    """

    name = "fake"

    def __init__(self, include_heading: bool = True, max_chars: int = 512) -> None:
        self.include_heading = include_heading
        self.max_chars = max_chars

    def available(self) -> bool:
        return True

    def status(self) -> str:
        return "fake：离线确定性替身（仅供测试与降级，不代表精排能力）"

    def rerank(self, query: str, candidates: Sequence[dict], top_k: int) -> list[dict]:
        q = set(_bigrams(query))
        scored: list[tuple[float, dict]] = []
        for c in candidates or []:
            p = set(_bigrams(_passage_text(c, self.include_heading, self.max_chars)))
            union = len(q | p)
            scored.append((round(len(q & p) / union, 6) if union else 0.0, c))
        # 稳定排序：分数相同时保持召回顺序（而不是变成任意顺序）
        scored.sort(key=lambda x: -x[0])
        return [{**c, "rerank_score": s} for s, c in scored[:top_k]]


class OnnxReranker:
    """真实 Cross-Encoder（ONNX 运行时，CPU）。**懒加载 + fail-open**。

    懒加载：`__init__` 只记录路径，第一次 `rerank()` 才建 session ——
    否则应用启动（含测试收集）会被一次 266MB 模型的加载拖住。

    fail-open：任何异常（文件损坏、ONNX 版本不兼容、分词器不匹配）都
    **返回原顺序**并把原因记进 `last_error`/`status()`，
    绝不让"精排坏了"升级成"检索坏了"。
    """

    name = "onnx"

    def __init__(
        self,
        model_dir: str | Path | None = None,
        include_heading: bool = False,
        max_chars: int = 512,
        max_length: int = 512,
    ) -> None:
        self.model_dir = Path(model_dir or settings.rerank_model_dir)
        self.include_heading = include_heading
        self.max_chars = max_chars
        self.max_length = max_length
        self.last_error = ""
        self._session = None
        self._tokenizer = None

    @property
    def model_path(self) -> Path:
        return self.model_dir / MODEL_FILE

    @property
    def tokenizer_path(self) -> Path:
        return self.model_dir / TOKENIZER_FILE

    def available(self) -> bool:
        return self.model_path.exists() and self.tokenizer_path.exists()

    def status(self) -> str:
        if self.last_error:
            return f"onnx：不可用（{self.last_error}）"
        if not self.available():
            return f"onnx：权重缺失（{self.model_dir}）→ 降级为不重排"
        return f"onnx：可用（{self.model_dir}）"

    def _load(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort
        from tokenizers import Tokenizer

        tok = Tokenizer.from_file(str(self.tokenizer_path))
        tok.enable_truncation(max_length=self.max_length)
        tok.enable_padding()
        self._tokenizer = tok
        self._session = ort.InferenceSession(
            str(self.model_path), providers=["CPUExecutionProvider"]
        )

    def rerank(self, query: str, candidates: Sequence[dict], top_k: int) -> list[dict]:
        items = list(candidates or [])
        if not items:
            return []
        try:
            self._load()
            return self._score_and_order(query, items, top_k)
        except Exception as exc:  # fail-open：精排失败不得升级为检索失败
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._session = None
            logger.warning("精排不可用，按原顺序返回：%s", self.last_error)
            return items[:top_k]

    # ---------------- 内部 ----------------

    def _score_and_order(self, query: str, items: list[dict], top_k: int) -> list[dict]:
        import numpy as np

        scores: list[float] = []
        for start in range(0, len(items), BATCH_SIZE):
            batch = items[start : start + BATCH_SIZE]
            pairs = [
                [query, _passage_text(c, self.include_heading, self.max_chars)] for c in batch
            ]
            encs = self._tokenizer.encode_batch(pairs)
            feeds = {
                "input_ids": np.array([e.ids for e in encs], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encs], dtype=np.int64),
            }
            out = self._session.run(None, feeds)
            scores.extend(float(v) for v in np.ravel(out[0]))
        order = sorted(range(len(items)), key=lambda i: -scores[i])
        return [{**items[i], "rerank_score": round(scores[i], 6)} for i in order[:top_k]]


def get_reranker() -> Reranker | None:
    """按 `RERANK_IMPL` 返回精排器；`off` 返回 None（调用方按不重排处理）。

    默认 `onnx`：**权重缺失时不自动换成 fake** —— 生产里"用假模型假装精排过"
    比"不精排"更糟（它让精度数字失去意义）。缺失由 `status()` 如实报出。
    """
    impl = (settings.rerank_impl or "off").strip().lower()
    if impl == "off":
        return None
    if impl == "fake":
        return FakeReranker(include_heading=settings.rerank_include_heading)
    return OnnxReranker(
        include_heading=settings.rerank_include_heading,
        max_chars=settings.rerank_max_chars,
    )


def _fetch(model_dir: str | Path | None = None) -> int:
    """下载权重（不随仓库走）。返回下载的字节数；已存在则跳过。

    写成 `.part` 再改名：中断不会留下半截文件被误判为"已下载"。
    """
    import shutil
    import urllib.request

    dst = Path(model_dir or settings.rerank_model_dir)
    dst.mkdir(parents=True, exist_ok=True)
    base = f"https://huggingface.co/{REMOTE_REPO}/resolve/main/"
    total = 0
    for remote, local in REMOTE_FILES:
        out = dst / local
        if out.exists() and out.stat().st_size > 1024:
            print(f"  已存在 {local}")
            continue
        tmp = dst / (local + ".part")
        try:
            with urllib.request.urlopen(base + remote, timeout=120) as resp, open(tmp, "wb") as f:
                shutil.copyfileobj(resp, f, 1024 * 1024)
            tmp.replace(out)
            total += out.stat().st_size
            print(f"  下载完 {local}（{out.stat().st_size / 1048576:.1f} MB）")
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            print(f"  跳过 {local}（{type(exc).__name__}）")
    return total


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="下载 ONNX 权重到 rerank_model_dir")
    ap.add_argument("--model-dir", default=None, help="权重目录（默认 settings.rerank_model_dir）")
    args = ap.parse_args()
    if args.fetch:
        _fetch(args.model_dir)
    else:
        print(get_reranker().status() if get_reranker() else "RERANK_IMPL=off（不重排）")
