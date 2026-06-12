"""本地 ONNX 向量模型 —— **不依赖任何供应商额度**的 embedding 实现。

## 为什么需要它（2026-06-16）

检索的向量通道一直寄在外部供应商（百炼 `text-embedding-v3`）上，账户欠费后
**整条语义检索静默停摆**，只剩关键词通道；而"充值才能检索"对一个要长期跑的
产品是结构性风险。实测确认 **DeepSeek 没有 embedding 接口**（`POST /embeddings` → 404），
所以"换个模型供应商"这条路对向量不成立 —— 只有本地模型或另找一家。

本模块走本地路线：与精排（`rerank.py`，也是 `onnxruntime` + `tokenizers`）**同一套依赖**，
没有引入任何新框架，只是多了一份权重文件。

## 模型约定（照 `rerank.py` 的 `data/models/<名字>/` 约定）

    data/models/bge-large-zh-v1.5/
    ├── model_int8.onnx       # 权重（int8 量化，约 330MB）
    ├── tokenizer.json        # 分词器（BGE 系 WordPiece，中文单字成词）
    ├── tokenizer_config.json
    └── config.json

## 为什么必须是 1024 维

`document_chunks.embedding` 的列类型写死为 `VECTOR(1024)`（`models.py` 的 `_PgVector(1024)`），
换模型若改维度就要动表结构。BGE 中文系列里 **`bge-large-zh-v1.5` = 1024 维**，正好对齐
（`bge-base-zh` 是 768、`bge-small-zh` 是 512，都会逼着改表）。

## 池化口径：CLS + L2 归一化

BGE 系列的标准用法 —— 取 **`[CLS]`（首 token）** 的隐状态，再 L2 归一化。
归一化之后余弦相似度即点积，与 `cosine_similarity` 的实现无关地一致。
"""

from __future__ import annotations

import pathlib
from typing import Sequence

from ..config import settings

#: 权重文件名（与 `onnx_fetch` 落盘的名字一致）
MODEL_FILE = "model_int8.onnx"
TOKENIZER_FILE = "tokenizer.json"

REMOTE_REPO = "Xenova/bge-large-zh-v1.5"
#: 只要**两个**文件：权重与分词器。`config.json` / `tokenizer_config.json` 是元数据，
#: `onnxruntime` 与 `tokenizers` 都不读它们 —— 列在这里只会让下载日志多两条"跳过"告警，
#: 而**噪声会训练人忽略告警**（那两个文件在部分镜像上确实不存在，实测）。
REMOTE_FILES = (
    ("onnx/model_int8.onnx", MODEL_FILE),
    (TOKENIZER_FILE, TOKENIZER_FILE),
)

#: BGE 上限 512 token；超过会截断（截断而非报错 —— 切片本身已按长度切好）。
MAX_LENGTH = 512

#: 每批条数。CPU 上一次 8 条与一次 1 条差别不大，但批量能省下多次 Python↔ONNX 往返。
BATCH = 8


class OnnxEmbedder:
    """本地 BGE 向量化器（**懒加载**：不构造就不加载 300MB 权重）。"""

    name = "local"

    def __init__(self, model_dir: str | pathlib.Path | None = None) -> None:
        self.model_dir = pathlib.Path(model_dir or settings.embedding_model_dir)
        self.last_error = ""
        self._session = None
        self._tokenizer = None
        self._input_names: set[str] = set()

    # ---------------- 状态 ----------------

    @property
    def model_path(self) -> pathlib.Path:
        return self.model_dir / MODEL_FILE

    @property
    def tokenizer_path(self) -> pathlib.Path:
        return self.model_dir / TOKENIZER_FILE

    def available(self) -> bool:
        return self.model_path.exists() and self.tokenizer_path.exists()

    def status(self) -> str:
        if self.last_error:
            return f"local：加载失败（{self.last_error[:80]}）"
        if not self.available():
            return (
                f"local：权重缺失（{self.model_dir}）→ 检索降级到关键词通道；"
                f"跑 `python -m app.services.local_embed --fetch` 下载"
            )
        return f"local：可用（{self.model_dir}）"

    # ---------------- 加载 ----------------

    def _load(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort
        from tokenizers import Tokenizer

        tok = Tokenizer.from_file(str(self.tokenizer_path))
        tok.enable_truncation(max_length=MAX_LENGTH)
        tok.enable_padding()
        self._tokenizer = tok
        self._session = ort.InferenceSession(
            str(self.model_path), providers=["CPUExecutionProvider"]
        )
        # 只喂模型**声明**要的输入：不同导出版本有的要 `token_type_ids`、有的不要，
        # 按声明取用比硬编码稳（硬编码的报错是 `invalid input name`，很难一眼看出原因）。
        self._input_names = {i.name for i in self._session.get_inputs()}

    # ---------------- 前向 ----------------

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量向量化，返回 **L2 归一化后的 1024 维**向量。"""
        import numpy as np

        self._load()
        assert self._session is not None and self._tokenizer is not None
        out: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            batch = [str(t or "") for t in texts[start : start + BATCH]]
            encs = self._tokenizer.encode_batch(batch)
            feeds: dict[str, "np.ndarray"] = {}
            if "input_ids" in self._input_names:
                feeds["input_ids"] = np.array([e.ids for e in encs], dtype=np.int64)
            if "attention_mask" in self._input_names:
                feeds["attention_mask"] = np.array(
                    [e.attention_mask for e in encs], dtype=np.int64
                )
            if "token_type_ids" in self._input_names:
                feeds["token_type_ids"] = np.array(
                    [e.type_ids for e in encs], dtype=np.int64
                )
            hidden = self._session.run(None, feeds)[0]

            if hidden.ndim == 2:
                # 导出时已带池化（sentence-transformers 变体）→ 直接用
                mat = hidden
            else:
                # `[CLS]` 池化：取首 token 的隐状态（BGE 的标准用法）
                mat = hidden[:, 0, :]
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            out.extend((mat / norms).astype("float32").tolist())
        return out

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


_EMBEDDER: OnnxEmbedder | None = None


def get_embedder() -> OnnxEmbedder | None:
    """取当前配置下的本地向量化器；未配置 local 模式时返回 `None`。

    ⚠️ `EMBEDDING_MODE=local` 是**显式开关**：不在这个模式下即使权重存在也不加载
    （测试套件要能完全离线跑，不能被一个 300MB 模型拖住）。
    """
    global _EMBEDDER
    if settings.embedding_mode != "local":
        return None
    if _EMBEDDER is None:
        _EMBEDDER = OnnxEmbedder()
    return _EMBEDDER


def reset_embedder() -> None:
    """丢弃缓存的模型实例（测试与"换了目录"后使用）。"""
    global _EMBEDDER
    _EMBEDDER = None


def fetch(model_dir: str | pathlib.Path | None = None) -> int:
    """下载权重（不随仓库走）。幂等，可反复跑。"""
    from .onnx_fetch import fetch_files

    return fetch_files(REMOTE_REPO, REMOTE_FILES, model_dir or settings.embedding_model_dir)


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="本地向量模型：下载 / 自检")
    ap.add_argument("--fetch", action="store_true", help="下载 ONNX 权重")
    ap.add_argument("--model-dir", default=None, help="权重目录（默认 settings.embedding_model_dir）")
    ap.add_argument("--check", action="store_true", help="加载模型并对一句话向量化（自检）")
    args = ap.parse_args()

    if args.fetch:
        fetch(args.model_dir)
    elif args.check:
        e = OnnxEmbedder(args.model_dir)
        print(e.status())
        if e.available():
            v = e.embed_one("教师享有教育教学权")
            print(f"  维度 {len(v)}　前 3 位 {[round(x, 4) for x in v[:3]]}")
    else:
        print(OnnxEmbedder(args.model_dir).status())
