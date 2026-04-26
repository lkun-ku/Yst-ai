"""官方语料入库：把 `backend/data/official/` 下的 Markdown 切成切片，写成 `Document(is_official=True)`。

**本模块存在的理由是「切分规则按语料类型分开」**，不是"又写了个解析器"：

| 语料 | 切分 | 为什么 |
| --- | --- | --- |
| **法条**（`laws/*.md`） | **按「第X条」切，一条一片** | ① 引用校验要能定位到条；② 条是法条的天然语义单元，一半的条文无法回溯 |
| **考纲 / rubric / 其它** | 按标题层级 + 滑窗（`doc_parser.split_chunks`） | 它们没有"条"这样的强单元，按标题切更自然 |

**幂等**：以 `storage_path`（相对语料根目录的路径）为键 —— 已存在则先删其旧切片再重建。
重复执行不会产生重复数据，考纲修订后重跑即可。

**为什么法条不复用通用切分器**：`split_chunks` 是"结构切分 + 滑窗"，滑窗会把一条条文切成两半，
直接破坏 `source_quote` 的子串校验前提（引用必须完整出现在某一片里）。

运行：

    python -m app.services.kb_corpus            # 灌入默认目录
    python -m app.services.kb_corpus --dry-run  # 只看会切出多少片，不写库
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from ..models import Document, DocumentChunk
from ..services.embedding import embed_one, encode_vector

#: 语料根目录（相对 backend/）。settings 优先，缺省回落到仓库内的 data/official。
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "official"

#: 法条的条号识别：`第一条` … `第八十六条`，也兼容 `第十三条` 这类两位中文数字。
_ARTICLE_RE = re.compile(r"^(第[一二三四五六七八九十百零〇\d]+条)(?:\s|　)*", re.MULTILINE)

#: 极简 frontmatter 解析（只取 key: value，够用且不为元数据引入新依赖）。
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class ChunkPlan:
    """一片待写入的切片。"""

    seq: int
    content: str
    heading_path: str | None


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def split_law_articles(title: str, body: str) -> list[ChunkPlan]:
    """把法条正文按「第X条」切成一片一条。

    条号前的**章标题**会并入 `heading_path`（如 `第二章 权利和义务 / 第七条`）——
    这样引用展示时能同时给出章与条，而正文仍只含本条内容。
    """
    plans: list[ChunkPlan] = []
    current_chapter = ""
    matches = list(_ARTICLE_RE.finditer(body))
    if not matches:
        return plans

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        segment = body[start:end]

        # 条号之前的最后一行若像章标题（`第X章`），记下来作为 heading 前缀
        before = body[:start].rstrip().splitlines()
        if before:
            last = before[-1].strip()
            if last.startswith("## "):
                last = last[3:].strip()
            if re.match(r"^第[一二三四五六七八九十]+章", last):
                current_chapter = last

        article = m.group(1)
        content = segment.strip()
        if not content:
            continue
        heading = f"{title} / {current_chapter} / {article}" if current_chapter else f"{title} / {article}"
        plans.append(ChunkPlan(seq=len(plans), content=content, heading_path=heading))
    return plans


def _split_general(title: str, body: str) -> list[ChunkPlan]:
    """非条文语料：按空行分段（考纲 / rubric 目前规模小，段落即语义单元）。"""
    plans: list[ChunkPlan] = []
    current_heading = title
    for para in body.split("\n\n"):
        text = para.strip()
        if not text:
            continue
        if text.startswith("#"):
            # 标题行单独成为下一批段落的 heading
            current_heading = text.lstrip("#").strip()
            continue
        plans.append(ChunkPlan(seq=len(plans), content=text, heading_path=current_heading))
    return plans


#: 这两个目录下的语料**按「第X条」切**（法律与行政法规都有"条"这个强单元）；
#: 其余目录（考纲 / rubric）走段落切分。
ARTICLE_DIRS = ("laws/", "regulations/")


def plan_file(rel_path: str, raw: str) -> list[ChunkPlan]:
    """一个 Markdown 文件 → 切片计划。

    目录约定：`laws/`（法律）与 `regulations/`（行政法规）按条切；其余按段切。
    """
    meta, body = _parse_frontmatter(raw)
    title = meta.get("law") or meta.get("short") or Path(rel_path).stem
    if rel_path.replace("\\", "/").startswith(ARTICLE_DIRS):
        return split_law_articles(title, body)
    return _split_general(title, body)


def iter_source_files(root: Path):
    """遍历语料根目录下的所有 `.md`（按路径排序，保证 seq 稳定）。"""
    for path in sorted(root.rglob("*.md")):
        yield path.relative_to(root).as_posix(), path


def ingest_official_corpus(db, root: str | Path | None = None, embed: bool = True) -> dict:
    """幂等灌入官方语料。返回 `{docs, chunks, skipped_empty}`。

    `embed=False` 时不算向量（仅建切片），用于快速验证切分规则 ——
    SQLAlchemy 侧 `embed_status` 记为 `pending`，后续可批量重算。
    """
    root_path = Path(root) if root else Path(settings.official_kb_dir or DEFAULT_ROOT)
    if not root_path.is_absolute():
        # 相对路径以 backend/ 为基准（与 settings 的其它相对路径约定一致）
        root_path = Path(__file__).resolve().parents[2] / root_path
    if not root_path.exists():
        return {"docs": 0, "chunks": 0, "skipped_empty": 0, "error": f"语料目录不存在：{root_path}"}

    stats = {"docs": 0, "chunks": 0, "skipped_empty": 0}
    is_pg = db.get_bind().dialect.name == "postgresql"

    for rel_path, abs_path in iter_source_files(root_path):
        raw = abs_path.read_text(encoding="utf-8")
        plans = plan_file(rel_path, raw)
        if not plans:
            stats["skipped_empty"] += 1
            continue

        # 幂等：同一 storage_path 的旧文档与其切片先删
        existing = (
            db.query(Document)
            .filter(Document.storage_path == rel_path, Document.is_official.is_(True))
            .one_or_none()
        )
        if existing is not None:
            db.query(DocumentChunk).filter(DocumentChunk.document_id == existing.id).delete(
                synchronize_session=False
            )
            db.delete(existing)
            db.flush()

        meta, _body = _parse_frontmatter(raw)
        doc = Document(
            candidate_id=None,  # 官方语料不属于任何考生
            is_official=True,
            title=meta.get("law") or meta.get("short") or Path(rel_path).stem,
            file_type="md",
            char_count=len(raw),
            chunk_count=len(plans),
            status="parsed",
            storage_path=rel_path,
        )
        db.add(doc)
        db.flush()  # 拿到 doc.id

        for plan in plans:
            raw_vec = embed_one(plan.content) if embed else None
            db.add(
                DocumentChunk(
                    document_id=doc.id,
                    seq=plan.seq,
                    content=plan.content,
                    heading_path=plan.heading_path,
                    char_count=len(plan.content),
                    embedding=raw_vec if is_pg else encode_vector(raw_vec),
                    embed_status="ok" if raw_vec else "pending",
                )
            )
        stats["docs"] += 1
        stats["chunks"] += len(plans)

    db.commit()
    return stats


if __name__ == "__main__":  # pragma: no cover
    import argparse

    from ..db import SessionLocal, init_db

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None, help="语料根目录（默认 settings.official_kb_dir）")
    ap.add_argument("--no-embed", action="store_true", help="只建切片不算向量")
    args = ap.parse_args()

    init_db()
    with SessionLocal() as db:
        result = ingest_official_corpus(db, root=args.root, embed=not args.no_embed)
        print(result)
