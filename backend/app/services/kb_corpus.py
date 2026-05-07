"""官方语料入库：把 `backend/data/official/` 下的 Markdown 切成切片，写成 `Document(is_official=True)`。

**本模块存在的理由是「切分规则按语料类型分开」**，不是"又写了个解析器"：

| 语料 | 切分 | 为什么 |
| --- | --- | --- |
| **法条**（`laws/*.md`） | **按「第X条」切，一条一片** | ① 引用校验要能定位到条；② 条是法条的天然语义单元，一半的条文无法回溯 |
| **考纲**（`syllabus/*.md`） | **按"叶子标题"切，一个考点一片** | 考纲没有"条"，但**考点**是它的天然单元（`（一）职业理念 / 2.学生观`）；见 `split_syllabus` |
| **rubric / 其它** | 按空行分段 | 规模小、结构弱，段落即单元 |

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


#: 章标题行（可能带 `#` 前缀）。用于把混进条文尾部的**下一章标题**切掉。
#: 限长 30 字是防误判：真正的章标题很短，而条文正文里不会出现"整行只有第X章…"的写法。
_CHAPTER_LINE_RE = re.compile(r"(?m)^\s*#{0,6}\s*第[一二三四五六七八九十]+章[\s　]*[^\n]{0,30}$")


def _trim_trailing_chapter(segment: str) -> str:
    """切掉条文尾部混入的**下一章标题**。

    **为什么必须切**：条文片段取的是「本条开头 → 下一条开头」，而这两条之间可能隔着
    下一章的标题（**章的首条**尤其常见，如第一章末条之后紧接 `## 第二章 权利和义务`）。
    不切的话，章标题会进入本条正文 —— 后果有两个：
    ① 检索污染：章标题成了该片内容，会被向量与关键词通道一起命中；
    ② **引用校验被绕过**：`source_quote` 可以引用一段章标题，
       而它会因为"确实出现在该片正文里"而通过硬校验 —— 这是个真实的漏洞。

    该方法由 `eval/citation_eval.py` 的基准跑出来的（一条"删中间字"用例漏放，
    追下去发现差异全在 `## 第二章` 这几个字符上），不是凭空加的防御。
    """
    hits = list(_CHAPTER_LINE_RE.finditer(segment))
    if hits:
        # 取**最后一个**：它才是本条与下一条之间的那道章边界
        segment = segment[: hits[-1].start()]
    return segment.strip()


def split_law_articles(title: str, body: str) -> list[ChunkPlan]:
    """把法条正文按「第X条」切成一片一条。

    条号前的**章标题**会并入 `heading_path`（如 `第二章 权利和义务 / 第七条`）——
    这样引用展示时能同时给出章与条，而正文只含本条内容（见 `_trim_trailing_chapter`）。
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
        content = _trim_trailing_chapter(segment)
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
#: 其余目录走各自的规则（`syllabus/` 按考点切、其它按段切）。
ARTICLE_DIRS = ("laws/", "regulations/")

#: 考纲与考试标准类语料：按**叶子标题**切（见 `split_syllabus`）。
SYLLABUS_DIRS = ("syllabus/",)

#: Markdown 标题行（捕获 `#` 个数与标题文字）。
_HEADING_RE = re.compile(r"^(#{1,6})[ \t　]*(.+?)[ \t　]*$", re.MULTILINE)


def split_syllabus(title: str, body: str) -> list[ChunkPlan]:
    """考纲：按**叶子标题**切，一个考点一片。

    ## 为什么不能沿用「按空行分段」

    `_split_general` 会把考纲切成一行一片：考察点「学生观」下有四条要求，
    它们会被拆成四个互不相干的片段 —— 检索时能命中某一条，但**丢了"这条要求属于哪个考点"**，
    而考纲的语义恰恰在归属上（「以人为本」是**学生观**的要求，不是教师观的）。
    一个考点一片，`heading_path` 才能同时给出模块与考点。

    ## 「叶子标题」是什么

    标题树里**后面直到同级或更高级标题之间没有更深标题**的那些 —— 也就是该分支的最末端。
    对本案的考纲：

    | 标题 | 是不是叶子 | 结果 |
    | --- | --- | --- |
    | `# 《综合素质》（中学）》` | 否 | 文档标题，只进 `title`，不单独成片 |
    | `## 一、考试目标` | **是**（下面没有 `###`） | 一片 |
    | `## 二、考试内容模块与要求` | 否（有 `###` 子节点） | 只作为 `heading_path` 的一环 |
    | `### （一）职业理念` | 否（有 `####` 子节点） | 同上 |
    | `#### 2.学生观` | **是** | 一片 ✅ |

    这条规则**同时**正确处理了结构不一致的文档：`### （四）文化素养` 在原文里没有子标题，
    它本身就是叶子 → 直接成一片。所以不必为每个文档手写层级假设。

    ## 内容里保留标题文字（但去掉 `#`）

    片正文以考点名开头（如 `2.学生观` 换行后接四条要求）—— 与法条片以 `第七条` 开头同理：
    引用校验是子串比对，用户引「2.学生观」或引某条要求，**两种都得能定位到**。
    `#` 是 Markdown 记号、不属于原文，故剥掉。
    """
    heads = [
        (m.start(), len(m.group(1)), m.group(2)) for m in _HEADING_RE.finditer(body)
    ]
    plans: list[ChunkPlan] = []

    for i, (start, level, text) in enumerate(heads):
        if level == 1:
            continue  # 文档标题：只作 title，不单独成片
        # 叶子判定：后面直到"同级或更高级"标题之间，不能再有更深的标题
        is_leaf = True
        for j in range(i + 1, len(heads)):
            if heads[j][1] <= level:
                break
            is_leaf = False
            break
        if not is_leaf:
            continue

        # 结束位置：下一个"同级或更高级"标题
        stop = len(body)
        for j in range(i + 1, len(heads)):
            if heads[j][1] <= level:
                stop = heads[j][0]
                break
        content = re.sub(r"^#+[ \t　]*", "", body[start:stop].strip())

        # 祖先链只取"模块"层级（level >= 3）：把 `## 二、考试内容模块与要求` 也塞进
        # heading_path 会让出处长到看不清，而它对本片几乎没有区分度 —— 每个考点都在它下面。
        ancestors: list[str] = []
        for j in range(i - 1, -1, -1):
            if heads[j][1] < level and heads[j][1] >= 3:
                ancestors.insert(0, heads[j][2])
            if heads[j][1] <= 2:
                break
        path = " / ".join([title, *ancestors, text]) if ancestors else f"{title} / {text}"
        plans.append(ChunkPlan(seq=len(plans), content=content, heading_path=path))

    return plans


def plan_file(rel_path: str, raw: str) -> list[ChunkPlan]:
    """一个 Markdown 文件 → 切片计划。

    目录约定：`laws/`（法律）与 `regulations/`（行政法规）按条切；
    `syllabus/`（考纲与考试标准）按叶子标题（考点）切；其余按段切。
    """
    meta, body = _parse_frontmatter(raw)
    title = meta.get("law") or meta.get("short") or Path(rel_path).stem
    norm = rel_path.replace("\\", "/")
    if norm.startswith(ARTICLE_DIRS):
        return split_law_articles(title, body)
    if norm.startswith(SYLLABUS_DIRS):
        return split_syllabus(title, body)
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
