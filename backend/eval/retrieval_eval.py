"""检索侧消融实验：稠密 → 稀疏 → RRF 融合 → + 标题加成。

与 `run_eval.py` 的分工：`run_eval.py` 测**生成质量与成本**（路线①②③ 对照）；
本脚本测**检索质量** —— 同一语料、同一标注集，只换检索配置。两者合起来才是完整消融矩阵
（`docs/改造计划.md` §3 第 2 项）。

**为什么必须直接调底层三件套**：`retrieve_by_scope` 把「向量 ⊕ 关键词 → RRF」封成了一个入口，
只跑它只能得到「融合后」的结果，回答不了「融合到底贡献了多少」。因此这里直接用
`vector_rank` / `keyword_rank` / `rrf` / `heading_bonus` 搭出各档配置。

---

## ⚠️ 读这张表之前必须先看的两条限制

1. **`EMBEDDING_MODE=fake` 时，本表不代表语义检索质量。**
   fake 模式的向量来自字符哈希伪向量（`embedding._fake_embed`），本质是词形匹配、没有语义。
   此时本表只证明「管线通、指标算得出、各档确实不同」，**不是**「稠密通道效果如何」的证据。
   要得到可对外引用的数字，须 `EMBEDDING_MODE=real`（+ key）后重跑。
2. **语料规模很小**（每域 6 片）。因此默认 ks=(1,2,3)，不取 k=10 ——
   6 片语料上 recall@10 恒为 1.0、没有信息量。语料扩充后应同步调整 ks
   （见 `metrics.DEFAULT_KS` 的说明）。

另：脚本会**重建 `eval_kb.db`**（与 `run_eval.py` 共用同一个评测库与语料灌入逻辑），
不触碰 `dev.db` / 测试库。

运行：
    python backend/eval/retrieval_eval.py                     # 默认 教资 域
    python backend/eval/retrieval_eval.py --domain 技术
    python backend/eval/retrieval_eval.py --out ../docs/eval  # 写一份可入库的基准快照
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

# 必须在导入 app 之前设定 DATABASE_URL（同 run_eval.py：避免污染 dev.db / 测试库）。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
os.environ["DATABASE_URL"] = os.environ.get("EVAL_DATABASE_URL") or "sqlite:///" + os.path.join(
    _ROOT, "backend", "eval", "eval_kb.db"
)

from app.db import SessionLocal, init_db  # noqa: E402
from app.services.embedding import embed_one  # noqa: E402
from app.services.kb_retrieval import (  # noqa: E402
    heading_bonus,
    keyword_rank,
    load_chunks,
    rrf,
    vector_rank,
)

try:  # 兼容「脚本直接运行」与「pytest 包上下文」两种方式
    from .metrics import DEFAULT_KS, evaluate_retrieval
    from .run_eval import DATASETS_DIR, _seed_dataset
except ImportError:  # pragma: no cover
    from eval.metrics import DEFAULT_KS, evaluate_retrieval  # noqa: E402
    from eval.run_eval import DATASETS_DIR, _seed_dataset  # noqa: E402


LABELS_FILE = "retrieval_labels.json"


def load_labels(dataset_dir: str) -> list[dict]:
    """读标注集；跳过以 `_` 开头的说明项（JSON 不支持注释，用保留键承载元信息）。"""
    with open(os.path.join(dataset_dir, LABELS_FILE), encoding="utf-8") as f:
        raw = json.load(f)
    return [item for item in raw if "query" in item]


def check_label_drift(labels: list[dict], chunks: list[dict]) -> list[tuple[str, str]]:
    """标注漂移自检：每条黄金片段必须真的能在语料中找到。

    语料一改（补内容 / 换文件），标注就会**静默失效** —— 指标照算，但算的是错的。
    这里把它变成一条显式检查：漂移即报出来，而不是让分数悄悄变低。
    """
    contents = [c.get("content") or "" for c in chunks]
    return [
        (item["query"], quote)
        for item in labels
        for quote in (item.get("gold") or [])
        if not any(quote in content for content in contents)
    ]


def build_configs(chunks: list[dict]) -> dict:
    """四档检索配置，每档一个 `query -> 有序切片正文列表` 的函数。

    档位顺序即「逐层叠加」，便于读出每一层的边际贡献：
      ① 稠密（向量）
      ② 稀疏（关键词）
      ③ ① ⊕ ② 经 RRF 融合
      ④ ③ + 标题路径加成（= 当前 SQLite 生产路径的行为）
    """
    def _content(index: int) -> str:
        return chunks[index].get("content") or ""

    def _vec_ranks(query: str):
        try:
            query_vec = embed_one(query)
        except Exception:  # embedding 失败 → 该通道缺席，由调用方的降级逻辑兜底
            query_vec = None
        return vector_rank(query_vec, chunks)

    def dense(query: str) -> list[str]:
        return [_content(ci) for _score, ci in _vec_ranks(query)]

    def sparse(query: str) -> list[str]:
        return [_content(ci) for _score, ci in keyword_rank(query, chunks)]

    def _fused(query: str, with_heading: bool) -> list[str]:
        vec, kw = _vec_ranks(query), keyword_rank(query, chunks)
        if not vec and not kw:
            return []  # 两路皆空：交给上层的均匀采样兜底，指标按未命中计
        merged = rrf([vec, kw])
        bonus = heading_bonus(query, chunks) if with_heading else {}
        order = sorted(merged, key=lambda ci: -(merged[ci] + bonus.get(ci, 0.0)))
        return [_content(ci) for ci in order]

    return {
        "① dense": dense,
        "② sparse": sparse,
        "③ +RRF": lambda q: _fused(q, False),
        "④ +RRF+标题加成": lambda q: _fused(q, True),
    }


def main(domain: str = "教资", out: str | None = None, ks=DEFAULT_KS) -> dict:
    db_file = os.path.join(_ROOT, "backend", "eval", "eval_kb.db")
    if os.path.exists(db_file):
        os.remove(db_file)
    init_db()

    out_dir = out or os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(out_dir, exist_ok=True)

    dataset_dir = os.path.join(DATASETS_DIR, domain)
    labels = load_labels(dataset_dir)

    db = SessionLocal()
    cand_id = 9200
    _seed_dataset(db, cand_id, dataset_dir)
    chunks = load_chunks(db, cand_id)

    drift = check_label_drift(labels, chunks)
    if drift:
        print(f"⚠️ 标注漂移：{len(drift)} 条黄金片段在语料里找不到（指标不可信）")
        for query, quote in drift:
            print(f"   - 「{query}」→ 找不到：{quote}")

    rows: dict[str, dict] = {}
    for name, retrieve in build_configs(chunks).items():
        m = evaluate_retrieval(labels, retrieve, ks=ks)
        rows[name] = m.as_row()
        metric_text = " | ".join(f"recall@{k}={v}" for k, v in m.recall_at_k.items())
        print(f"{name:>18} | n={m.n_queries} | {metric_text} | mrr={m.mrr} | ndcg={m.ndcg}")

    mode = (
        f"LLM={os.environ.get('LLM_MODE', 'fake')}, "
        f"Embedding={os.environ.get('EMBEDDING_MODE', 'fake')}"
    )
    result = {
        "metadata": {
            "time": datetime.now().isoformat(timespec="seconds"),
            "domain": domain,
            "n_chunks": len(chunks),
            "n_queries": len(labels),
            "ks": list(ks),
            "mode": mode,
            "label_status": "预标（待人工抽检）",
            "drift": len(drift),
        },
        "rows": rows,
    }

    _write_markdown(os.path.join(out_dir, "retrieval_baseline.md"), result)
    with open(os.path.join(out_dir, "retrieval_baseline.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n已写入：{os.path.join(out_dir, 'retrieval_baseline.md')}")
    return result


def _write_markdown(path: str, result: dict) -> None:
    meta = result["metadata"]
    ks = meta["ks"]
    lines = [
        "# 检索侧基准（消融）",
        "",
        f"- 生成时间：{meta['time']}",
        f"- 领域：{meta['domain']}　语料：{meta['n_chunks']} 片　查询：{meta['n_queries']} 条",
        f"- 运行模式：{meta['mode']}",
        f"- 标注状态：{meta['label_status']}　漂移条数：{meta['drift']}",
        "",
        "> ⚠️ `Embedding=fake` 时的向量来自字符哈希伪向量，本质是词形匹配、没有语义。",
        "> 此时本表只证明「管线通、指标算得出、各档确实不同」，**不是**稠密通道效果的证据。",
        "> 语料规模小时不取 k=10（6 片语料上 recall@10 恒为 1.0）。",
        "",
        "| 配置 | " + " | ".join(f"recall@{k}" for k in ks) + " | MRR | nDCG |",
        "| --- | " + " | ".join("---" for _ in ks) + " | --- | --- |",
    ]
    for name, row in result["rows"].items():
        cells = " | ".join(str(row[f"recall@{k}"]) for k in ks)
        lines.append(f"| {name} | {cells} | {row['mrr']} | {row['ndcg']} |")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="教资", help="datasets/ 下的领域目录名")
    ap.add_argument("--out", default=None, help="结果输出目录（默认 eval/results/）")
    args = ap.parse_args()
    main(domain=args.domain, out=args.out)
