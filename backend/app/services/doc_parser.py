"""文档解析与切分（AI 出题模块）。

管线位置：解析 → 清洗 → 两级切分 →（下游 embedding）

设计要点：
- **pypdf（BSD）而非 PyMuPDF（AGPL）**：商业化许可干净；中文 PDF 常见的断行、
  页眉页脚、双栏乱序用后处理清洗补偿。
- **不做 OCR**：扫描版 PDF 只做无文字层检测并提示。
- **两级切分**：先按标题切段（记 `heading_path`），段内滑窗（默认 1500/200）。
  PDF 无可靠标题结构时 `heading_path` 为 None，由下游向量检索兜底。
"""

from __future__ import annotations

import re
from collections import Counter

SUPPORTED_TYPES = ("pdf", "docx", "txt", "md")

#: 每页平均字符低于此值，判定为扫描件（无文字层）
NO_TEXT_AVG_CHARS = 20

# 标题识别：Markdown `#`、中文「第X章/节/篇」、数字编号「1.2.3」
_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s+\S+|第[0-9一二三四五六七八九十百]+[章节篇]\s*\S*|[0-9]+(?:\.[0-9]+){0,3}\s+\S+)"
)
_PAGE_NUM_RE = re.compile(r"^[-–—\s]*\d{1,4}[-–—\s]*$")
_SENT_END = "。！？；!?;：:”’）)】》"


# ---------------- 清洗 ----------------

def clean_text(text: str | None) -> str:
    """清洗抽取文本：剔除重复页眉页脚、纯页码行，并合并被拆断的中文行。"""
    if not text:
        return ""
    lines = [ln.strip() for ln in str(text).splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return ""

    # 剔除高频重复的页眉/页脚：出现次数达到阈值且较短的行
    if len(lines) >= 10:
        counts = Counter(lines)
        threshold = max(3, len(lines) // 10)
        repeated = {ln for ln, c in counts.items() if c >= threshold and len(ln) <= 30}
        if repeated:
            lines = [ln for ln in lines if ln not in repeated]

    # 过滤纯页码行
    lines = [ln for ln in lines if not _PAGE_NUM_RE.match(ln)]

    # 合并断行
    merged: list[str] = []
    for ln in lines:
        if merged and _should_merge(merged[-1], ln):
            merged[-1] = merged[-1] + ln
        else:
            merged.append(ln)
    return "\n".join(merged)


def _should_merge(prev: str, cur: str) -> bool:
    """判断 cur 是否是 prev 被拆断的续行。"""
    if not prev or not cur:
        return False
    # 上一行以标题结尾 / 当前行是标题 → 都不合并（否则会破坏 heading_path）
    if _HEADING_RE.match(cur) or _HEADING_RE.match(prev):
        return False
    if prev[-1] in _SENT_END:
        return False
    return bool(re.match(r"[\u4e00-\u9fff]", cur[0]))


# ---------------- 无文字层检测 ----------------

def detect_no_text_layer(text: str | None, page_count: int | None) -> bool:
    """按「每页平均字符数」判断是否为扫描件。"""
    pages = max(1, int(page_count or 0))
    avg = len((text or "").strip()) / pages
    return avg < NO_TEXT_AVG_CHARS


# ---------------- 切分 ----------------

#: 拼接全文时允许消掉的最大重复长度。取滑窗 overlap 的两倍余量。
_RESTITCH_MAX_OVERLAP = 400


def join_chunks(contents: list[str], max_overlap: int = _RESTITCH_MAX_OVERLAP) -> str:
    """把切片按 seq 拼回连续全文，**消除滑窗重叠**。

    为什么需要（2026-06-15 实测反馈）：资料详情页只给切片预览，用户看不到原文；
    而 `split_chunks` 是**滑窗切分**（默认 `overlap=200`），直接把切片首尾相接，
    每个边界处那 ~200 字会被**重复一遍** —— 用户会以为文件本身写重了。
    这比不给全文更糟：损坏的文本让人无法判断到底是资料问题还是产品问题。

    做法：相邻两片，取上一片**最长的后缀**去匹配下一片的**前缀**，命中即裁掉重复段。
    只在「0 < 重复长度 ≤ `max_overlap`」时裁切 —— 上限是为了防"两片真的碰巧首尾相同很长"
    那种情形：裁掉会**丢内容**，而丢内容比留一段重复更难被发现。
    """
    out = ""
    for raw in contents or []:
        cur = raw or ""
        if not cur:
            continue
        if not out:
            out = cur
            continue
        best = 0
        for k in range(min(len(out), len(cur), max_overlap), 0, -1):
            if out.endswith(cur[:k]):
                best = k
                break
        out += cur[best:]
    return out


def split_chunks(
    text: str | None,
    chunk_size: int = 1500,
    overlap: int = 200,
) -> list[dict]:
    """两级切分：结构切分（带 heading_path）+ 滑窗。

    返回 `[{"seq","content","heading_path","char_count"}]`。
    """
    body = clean_text(text)
    if not body:
        return []

    chunks: list[dict] = []
    seq = 0
    for path, segment in _split_by_heading(body):
        for piece in _sliding_window(segment, chunk_size, overlap):
            chunks.append(
                {
                    "seq": seq,
                    "content": piece,
                    "heading_path": path,
                    "char_count": len(piece),
                }
            )
            seq += 1
    return chunks


def _split_by_heading(text: str) -> list[tuple[str | None, str]]:
    """按标题切段，返回 [(heading_path, segment_text)]。无标题时为 (None, 全文)。"""
    lines = text.splitlines()
    segments: list[tuple[str | None, list[str]]] = []
    current_path: str | None = None

    for ln in lines:
        if _HEADING_RE.match(ln):
            current_path = ln.strip()
            segments.append((current_path, []))
            continue
        if not segments:
            segments.append((None, []))
        segments[-1][1].append(ln)

    return [(path, "\n".join(body).strip()) for path, body in segments if "\n".join(body).strip()]


def _sliding_window(text: str, size: int, overlap: int) -> list[str]:
    if size <= 0:
        return [text]
    if len(text) <= size:
        return [text]
    step = max(1, size - max(0, overlap))
    out: list[str] = []
    start = 0
    while start < len(text):
        out.append(text[start : start + size])
        start += step
    return out


# ---------------- 去重归一化 ----------------

def normalize_stem(stem: str | None) -> str:
    """题干归一化（用于跨批去重）：去空白、全角标点转半角、统一小写。"""
    if not stem:
        return ""
    s = str(stem)
    for full, half in (("？", "?"), ("，", ","), ("：", ":"), ("；", ";"), ("！", "!")):
        s = s.replace(full, half)
    s = re.sub(r"\s+", "", s)
    return s.lower()


# ---------------- 文件解析 ----------------

def parse_document(path: str, file_type: str) -> dict:
    """解析文件为纯文本。

    返回 `{"text","page_count","no_text_layer"}`。`text` 已清洗。
    """
    ft = (file_type or "").lower().strip()
    if ft == "pdf":
        return _parse_pdf(path)
    if ft == "docx":
        return _parse_docx(path)
    return _parse_text(path)


def _parse_pdf(path: str) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")  # 单页解析失败不中断整体
    raw = "\n".join(pages)
    return {
        "text": clean_text(raw),
        "page_count": len(pages),
        # 用未清洗的 raw 判定，避免清洗把"内容很少"放大成"有内容"
        "no_text_layer": detect_no_text_layer(raw, len(pages)),
    }


def _parse_docx(path: str) -> dict:
    import docx

    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs]
    # 表格内容一并纳入（讲义常用表格承载知识点）
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" ".join(cells))
    raw = "\n".join(parts)
    return {"text": clean_text(raw), "page_count": 0, "no_text_layer": detect_no_text_layer(raw, 1)}


def _parse_text(path: str) -> dict:
    data = open(path, "rb").read()
    text = None
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", errors="ignore")
    return {"text": clean_text(text), "page_count": 0, "no_text_layer": detect_no_text_layer(text, 1)}
