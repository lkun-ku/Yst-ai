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
import hashlib
import json
import os
import re
import sys
from datetime import datetime

# 必须在导入 app 之前设定 DATABASE_URL（同 run_eval.py：避免污染 dev.db / 测试库）。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
os.environ["DATABASE_URL"] = os.environ.get("EVAL_DATABASE_URL") or "sqlite:///" + os.path.join(
    _ROOT, "backend", "eval", "eval_kb.db"
)

from app.db import SessionLocal, init_db  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.embedding import BigramBM25, embed_one  # noqa: E402
from app.services.kb_corpus import ingest_official_corpus  # noqa: E402
from app.services.kb_retrieval import (  # noqa: E402
    heading_bonus,
    keyword_rank,
    keyword_rank_bm25,
    load_chunks,
    load_chunks_for_scope,
    rrf,
    vector_rank,
)
from app.services.scope import NAMESPACE_OFFICIAL, Scope  # noqa: E402

try:  # 兼容「脚本直接运行」与「pytest 包上下文」两种方式
    from .metrics import evaluate_retrieval, pick_ks
    from .run_eval import DATASETS_DIR, _seed_dataset
except ImportError:  # pragma: no cover
    from eval.metrics import evaluate_retrieval, pick_ks  # noqa: E402
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


_ART_HEAD_RE = re.compile(r"^第[一二三四五六七八九十百零〇\d]+条[\s　]*")


def labels_fingerprint(labels: list[dict]) -> str:
    """标注集的指纹（用于**跨进程可复现性**自检）。

    只对 `query` / `gold` 取摘要，**不含 `note`** —— 说明文字是给人看的，
    改文案不该让指纹变化（否则自检会因为改注释而失败，变成噪声）。

    它存在的理由是一次真实的踩坑：程序化标注曾因排序键不完整而**跨进程不同**
    （详见 `build_law_labels` 里的说明），而那种漂移**不会报错**，
    只会让基准数字每次运行都不一样 —— 正是最难发现的一类问题。
    """
    blob = json.dumps(
        [(x.get("query"), x.get("gold")) for x in labels], ensure_ascii=False
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def build_law_labels(chunks: list[dict], limit: int = 120) -> list[dict]:
    """程序化生成法条标注（条号 + 高频繁主题词 → 目标条文的**唯一**片段）。

    **为什么需要它**：手写标注只有 15 条，而 n=15 时**一条查询就是 6.7 个百分点** ——
    0.9333 与 1.0 之间的"提升"其实只差一条查询，完全落在噪声里。
    要用指标判断稀疏通道升级是否有效，样本量必须先够；否则"有效"与"无效"都只是感觉。

    **它不代表真实用户问法**（手写子集才是），它只负责两件事：
    把样本量拉到达可辨识的量级；并**刻意构造**"主题词在全语料高频、只有条号唯一"的形态 ——
    那正是「纯向量对编号类查询必然失败」的场景，也是稀疏通道权重的靶点。

    两条自检保证标注本身是对的（错了会让指标悄悄失真）：

    ① **黄金片段在语料中精确唯一** —— 否则"命中"可能来自另一条条文，召回被高估；
    ② **黄金片段不出现在 query 里** —— 否则等于把答案抄进问题，测的不是检索。
    """
    from app.services.embedding import query_terms

    contents = [c.get("content") or "" for c in chunks]
    df: dict[str, int] = {}
    for text in contents:
        for t in set(query_terms(text)):
            df[t] = df.get(t, 0) + 1

    labels: list[dict] = []
    for i, c in enumerate(chunks):
        parts = (c.get("heading_path") or "").split(" / ")
        if len(parts) < 2:
            continue
        law = parts[0].replace("中华人民共和国", "")
        article = parts[-1]
        body = _ART_HEAD_RE.sub("", contents[i]).strip()
        if len(body) < 40:
            continue

        # 主题词：本条出现、且在全语料高频（≥5 片）的二元组 —— 高频才有"竞争"，才测得准。
        #
        # ⚠️ 排序键**必须完全确定**：只按 df 排序时，df 相同的词项其先后取决于 `set` 的
        # 迭代顺序，而 set 的迭代顺序取决于字符串哈希 —— CPython **默认按进程随机化**
        # （PYTHONHASHSEED），于是**跨进程生成的标注就不同，基准数字也随之不可复现**。
        # 补上 `t` 作次级键后排序完全确定。
        # （本项踩过：同一份代码、同一个域，两次跑出 recall@1 = 0.6667 与 0.3037。）
        topics = sorted(
            (t for t in set(query_terms(body)) if df.get(t, 0) >= 5),
            key=lambda t: (-df.get(t, 0), t),
        )[:3]
        if not topics:
            continue

        # 黄金片段：正文中段取 16 字，要求全语料精确唯一
        gold = ""
        for start in (len(body) // 3, len(body) // 2, len(body) // 4):
            frag = body[start : start + 16]
            if len(frag) >= 12 and sum(1 for t in contents if frag in t) == 1:
                gold = frag
                break
        if not gold:
            continue

        query = f"{law}{article} {' '.join(topics)}"
        if gold in query:
            continue
        labels.append(
            {
                "query": query,
                "gold": [gold],
                "kind": "编号类·程序生成",
                "note": f"高频主题词={topics}（df≥5，刻意制造竞争）",
            }
        )
        if len(labels) >= limit:
            break
    return labels


def _rerank_tier(chunks: list[dict], reranker, pool: int):
    """「稀疏召回 → 精排」这一档：粗排取前 `pool` 条，精排器重排后返回正文列表。

    **为什么建在稀疏通道上、而不是生产融合链路上**：`EMBEDDING_MODE=fake` 时稠密通道
    是字符哈希噪声（见本模块 docstring 的警告）—— 建在它上面的数字**不可复现也不可解释**。
    稀疏通道是确定性的，且它在编号类查询上恰是最强通道（recall@5 = 0.9778），
    上限清晰（`recall@pool − recall@1`），因此是能承载结论的那条基准线。
    """

    def f(query: str) -> list[str]:
        cands = [chunks[i] for _s, i in keyword_rank(query, chunks)[:pool]]
        return [c["content"] for c in reranker.rerank(query, cands, len(cands))]

    return f


def build_configs(chunks: list[dict], rerankers: dict | None = None) -> dict:
    """七档检索配置，每档一个 `query -> 有序切片正文列表` 的函数。

    档位顺序即「逐层叠加」，便于读出每一层的边际贡献：

    | 档 | 内容 |
    | --- | --- |
    | ① | 稠密（向量） |
    | ② | 稀疏·命中数（**生产口径**） |
    | ③ | 稀疏·BM25（**未采纳**，见下） |
    | ④ | ① ⊕ ② 经 RRF |
    | ⑤ | ④ + 标题路径加成 ← **当前生产行为（基准）** |
    | ⑥ | ① ⊕ ③ 经 RRF |
    | ⑦ | ⑥ + 标题路径加成 |

    **③⑥⑦ 是"被否决的升级"的留档**：BM25 曾被认为能靠 IDF 提升编号类查询，
    实测却只有命中数口径一半以下的 recall@1（见 `embedding.BigramBM25` 的 docstring）。
    把这三档留在表里而不是删掉代码，是为了：① 回退决策有据可查；
    ② 语料规模变化后可以原地重跑重判。删掉它们等于把"我们试过、为什么不行"一起删掉。
    """
    # BM25 的语料级统计（df / 平均长度）只依赖切片集合，与 query 无关 → 建一次复用
    bm25 = BigramBM25.fit([c.get("content") or "" for c in chunks])

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

    def sparse_hits(query: str) -> list[str]:
        return [_content(ci) for _score, ci in keyword_rank(query, chunks)]

    def sparse_bm25(query: str) -> list[str]:
        return [_content(ci) for _score, ci in keyword_rank_bm25(query, chunks, index=bm25)]

    def _fused(query: str, use_bm25: bool, with_heading: bool) -> list[str]:
        vec = _vec_ranks(query)
        kw = keyword_rank_bm25(query, chunks, index=bm25) if use_bm25 else keyword_rank(query, chunks)
        if not vec and not kw:
            return []  # 两路皆空：交给上层的均匀采样兜底，指标按未命中计
        merged = rrf([vec, kw])
        bonus = heading_bonus(query, chunks) if with_heading else {}
        order = sorted(merged, key=lambda ci: -(merged[ci] + bonus.get(ci, 0.0)))
        return [_content(ci) for ci in order]

    row = {
        "① dense": dense,
        "② sparse·命中数": sparse_hits,
        "③ sparse·BM25": sparse_bm25,
        "④ RRF(命中数)": lambda q: _fused(q, False, False),
        "⑤ RRF(命中数)+标题 ★生产": lambda q: _fused(q, False, True),
        "⑥ RRF(BM25)": lambda q: _fused(q, True, False),
        "⑦ RRF(BM25)+标题": lambda q: _fused(q, True, True),
    }
    # 精排档：按调用方给的 {标签: 精排器} 追加（未开启精排时不出现，避免表里出现空档）
    for label, rk in (rerankers or {}).items():
        row[label] = _rerank_tier(chunks, rk, settings.rerank_pool)
    return row


def _build_rerankers() -> dict:
    """两种口径的精排器：**仅正文** / **含标题**。

    **为什么必须两种都报**：法条的 `heading_path` 形如
    「中华人民共和国教师法 / 第一章 总则 / 第七条」，而编号类 query 恰是
    「教师法 + 第七条」—— 把标题喂进去等于**把答案抄给模型**。
    只报含标题那一档，精排增益会虚高；两档并排才看得出
    "增益里有多少来自元数据、有多少来自 Cross-Encoder 的语义"。
    """
    from app.services.rerank import OnnxReranker

    return {
        "⑧ sparse→精排(仅正文)": OnnxReranker(
            include_heading=False, max_chars=settings.rerank_max_chars
        ),
        "⑨ sparse→精排(含标题)": OnnxReranker(
            include_heading=True, max_chars=settings.rerank_max_chars
        ),
    }


def main(
    domain: str = "教资",
    out: str | None = None,
    ks=None,
    official: bool = False,
    auto: bool = False,
    rerank: bool = False,
) -> dict:
    """跑一个领域的检索消融表。

    `official=True` 时把 `backend/data/official/` 灌成**官方语料**并用 `official`
    命名空间加载（法条域）—— 编号/条款类查询的评测必须建在法条语料上，
    而它属于命名空间 `official`，与个人资料库的加载路径不同。

    `auto=True` 时把手写标注与**程序化标注**（`build_law_labels`）合并统计。
    手写子集代表真实问法但只有 15 条（一条查询 = 6.7 个百分点，噪声太大）；
    合并后样本量够用，但**问法不代表真实用户** ——
    两个口径要分别看，别只报好看的那个。
    """
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
    ingest_stats = None
    if official:
        ingest_stats = ingest_official_corpus(db, embed=True)
        chunks = load_chunks_for_scope(db, Scope(namespace=NAMESPACE_OFFICIAL))
    else:
        _seed_dataset(db, cand_id, dataset_dir)
        chunks = load_chunks(db, cand_id)

    # k 按**语料规模**取，不写死：语料 6 片时 k=10 恒为 1.0、没有信息量；
    # 而官方法条有 415 片，那里 k=5/10 才是有效档位（§6.3 承诺的正是 K=1/5/10）。
    ks = tuple(ks) if ks else pick_ks(len(chunks))
    print(f"[ks] 语料 {len(chunks)} 片 → 考察 k={list(ks)}（不变量：max(k) < 语料规模）")

    n_handwritten = len(labels)
    if auto:
        labels = labels + build_law_labels(chunks)

    drift = check_label_drift(labels, chunks)
    if drift:
        print(f"⚠️ 标注漂移：{len(drift)} 条黄金片段在语料里找不到（指标不可信）")
        for query, quote in drift:
            print(f"   - 「{query}」→ 找不到：{quote}")

    rerankers = _build_rerankers() if rerank else {}
    rerank_status = {}
    for label, rk in rerankers.items():
        rerank_status[label] = rk.status()
        print(f"[精排] {label} → {rk.status()}")

    rows: dict[str, dict] = {}
    for name, retrieve in build_configs(chunks, rerankers).items():
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
            "corpus": "official（法条）" if official else "personal（评测语料）",
            "n_chunks": len(chunks),
            "n_queries": len(labels),
            "n_labels_handwritten": n_handwritten,
            "n_labels_auto": len(labels) - n_handwritten,
            "ks": list(ks),
            "mode": mode,
            "label_status": "预标（待人工抽检）" if not auto else "手写 + 程序化（问法不代表真实用户）",
            "labels_fingerprint": labels_fingerprint(labels),
            "drift": len(drift),
            "ingest": ingest_stats,
            "rerank_pool": settings.rerank_pool if rerankers else None,
            "rerank_status": rerank_status,
        },
        "rows": rows,
    }

    # 文件名带领域：两个域跑一次会**互相覆盖**，而覆盖掉的是刚跑出来的证据
    # （本项就踩过一次：教资域的表把法条域的表冲掉了）。基准表按领域分开存。
    stem = f"retrieval_baseline_{domain}"
    _write_markdown(os.path.join(out_dir, f"{stem}.md"), result)
    with open(os.path.join(out_dir, f"{stem}.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n已写入：{os.path.join(out_dir, stem + '.md')}")
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
        f"- 标注指纹（跨进程可复现性）：`{meta.get('labels_fingerprint', '')}`",
        "",
        "> ⚠️ **`Embedding=fake` 时，①④⑤⑥⑦ 这五行（稠密与融合）的数字不可解释**：",
        "> 伪向量是「字符 → 哈希桶」的计数，本质是**噪声**，不含任何语义。",
        "> 实测后果：同一份代码、同一个域，两次运行 `recall@1` 可从 0.43 掉到 0.01 ——",
        "> 查询词稍变，排序就完全变样。**因此本表只有稀疏档（②③）的对比可用于判断**，",
        "> 稠密与融合档须 `EMBEDDING_MODE=real` 后重跑才有意义。",
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
    ap.add_argument(
        "--official",
        action="store_true",
        help="用法条语料跑（灌入 data/official/，按 official 命名空间检索）",
    )
    ap.add_argument(
        "--auto",
        action="store_true",
        help="把手写标注与程序化标注合并统计（样本量够，但问法不代表真实用户）",
    )
    ap.add_argument(
        "--rerank",
        action="store_true",
        help="追加精排档（需要 ONNX 权重；缺权重时自动降级为不重排，表中档位仍会列出）",
    )
    args = ap.parse_args()
    main(
        domain=args.domain,
        out=args.out,
        official=args.official,
        auto=args.auto,
        rerank=args.rerank,
    )
