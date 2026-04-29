"""引用硬校验（`source_quote`）：把「不许编造」变成会失败的断言。

**为什么单列一组用例**：这是本产品唯一宣称"致命风险可控"的地方（法条零编造）。
提示词、自检、投票都可能失效且**不会报错**，只有子串校验是确定性的 ——
所以它必须被钉死：判定层级、边界、闸门行为、成对指标的口径，逐条断言。

覆盖六件事：

1. 归一化与分层判定（`exact` / `normalized` / `fabricated` / `missing`）；
2. **编号级篡改必被拦**（`《教师法》第九十九条 + 第七条的正文` 这类最致命的错配）；
3. 引用字段的两种写法（`source_quote` / `source_quotes`）都要认；
4. 闸门行为（编造必拦；未给引用是否拦由 `require_quote` 决定）；
5. **成对指标**：只看"流出编造率 = 0"是自欺，必须同时要求"拦截率 > 0"；
6. 假客户端也要给**可校验的**引用，否则 `LLM_MODE=fake` 下闸门会拦掉全部题、
   测试再也覆盖不到下游链路。
"""

import json

import pytest

from app.config import settings
from app.services.citation import (
    QUOTE_FIELD,
    STATUS_EXACT,
    STATUS_FABRICATED,
    STATUS_MISSING,
    STATUS_NORMALIZED,
    STATUS_TOO_SHORT,
    CitationReport,
    normalize_for_match,
    quotes_of,
    split_by_citation,
    verify_payloads,
    verify_quote,
)
from app.services.llm_client import FakeLLMClient, _quote_from_prompt
from app.services.prompts_kb import kb_question_prompt

#: 一条真实的法条切片（含条号 —— 切片正文本来就以条号开头）。
_LAW = "第七条 教师享有下列权利：\n\n（一）进行教育教学活动，开展教育教学改革和实验；\n\n（二）从事科学研究、学术交流。"
_OTHER = "第八条 教师应当履行下列义务：\n\n（一）遵守宪法、法律和职业道德，为人师表；"
_CHUNKS = [{"id": 101, "content": _LAW}, {"id": 102, "content": _OTHER}]


# ---------------- 1. 归一化与分层判定 ----------------

def test_归一化只保留可读字符():
    """标点、空白、书名号、全角半角都不参与内容比对 —— 它们不构成内容差异。"""
    assert normalize_for_match("第七条 教师享有下列权利：") == "第七条教师享有下列权利"
    assert normalize_for_match("《中华人民共和国教师法》第七条") == "第七条"
    assert normalize_for_match("ＡＢＣ１２３") == "abc123"


def test_逐字摘录判定为_exact():
    chk = verify_quote(_LAW[:30], _CHUNKS)
    assert chk.status == STATUS_EXACT
    assert chk.chunk_id == 101
    assert chk.ok and not chk.fabricated


def test_改标点与改断行仍可定位_但降级为_normalized():
    """容忍"只改写法"是刻意的：不这样做会把一批真实引用误判为编造。"""
    for variant in (
        _LAW[:30].replace("：", ":"),
        _LAW[:30].replace("\n\n", "\n").replace(" ", ""),
    ):
        chk = verify_quote(variant, _CHUNKS)
        assert chk.status == STATUS_NORMALIZED, variant
        assert chk.ok


def test_带书名号出处前缀仍可定位():
    """`《教师法》第七条：…` 是模型最自然的写法，前缀是元数据（正文里没有）。"""
    body = _LAW.split(" ", 1)[1][:20]
    chk = verify_quote(f"《中华人民共和国教师法》第七条：{body}", _CHUNKS)
    assert chk.status == STATUS_NORMALIZED
    assert chk.ok


def test_空引用判定为_missing():
    """没给引用不等于编造 —— 两者必须分开，否则会把"漏字段"误判成"编造"。"""
    assert verify_quote("", _CHUNKS).status == STATUS_MISSING
    assert verify_quote("   ", _CHUNKS).status == STATUS_MISSING


def test_过短引用被拦下但不算编造():
    """`第七条` 在任何法条库里都能命中，不能作为证据 —— 由 min_chars 政策拦下。

    **但它不算编造**：它在原文里逐字存在。若把这个政策性拦截混进"零编造"指标，
    那个数字就变得不诚实（把"引用太短"和"凭空捏造"算成一回事）。
    """
    chk = verify_quote("第七条", _CHUNKS)
    assert chk.status == STATUS_TOO_SHORT
    assert chk.too_short and not chk.fabricated and not chk.ok
    # 过短检查**先于** exact —— 否则三字引用会因逐字存在而放行，闸门形同虚设
    _, blocked, report = split_by_citation([{QUOTE_FIELD: "第七条", "stem": "X"}], _CHUNKS)
    assert [p["stem"] for p in blocked] == ["X"]
    assert report.n_too_short == 1 and report.n_fabricated == 0 and report.hit_rate == 0.0
    # 边界：刚好达标的长度应通过
    assert verify_quote("第七条 教师享", _CHUNKS, min_chars=6).ok


# ---------------- 2. 编号级篡改（最致命的一类） ----------------

def test_换条号必被拦_即使正文逐字正确():
    """正文对、条号错 —— 这是引用校验存在的**首要理由**。

    若剥掉条号再比对，这条会被放过：它能通过"正文确实在库里"的检查，
    却把结论挂到了错误的法律条文上。
    """
    body = _LAW.split(" ", 1)[1][:20]
    chk = verify_quote(f"第八条 {body}", _CHUNKS)
    assert chk.status == STATUS_FABRICATED
    assert chk.fabricated


def test_换词与拼接必被拦():
    assert verify_quote("第七条 教师享有下列义务：", _CHUNKS).fabricated
    assert verify_quote("第七条 遵守宪法、法律和职业道德", _CHUNKS).fabricated


def test_凭空编造必被拦():
    assert verify_quote("第八十七条 教师有权自行决定教学内容。", _CHUNKS).fabricated


def test_切片可以是裸字符串():
    chk = verify_quote(_LAW[:20], [_LAW])
    assert chk.status == STATUS_EXACT
    assert chk.chunk_id is None  # 裸字符串没有 id


# ---------------- 3. 引用字段的两种写法 ----------------

def test_引用字段的两种写法都要认():
    assert quotes_of({QUOTE_FIELD: "第七条 教师享有下列权利"}) == ["第七条 教师享有下列权利"]
    assert quotes_of({"source_quotes": ["a", "b"]}) == ["a", "b"]
    # 两种同时出现时合并；空串与纯空白丢弃（它们不是引用）
    assert quotes_of({QUOTE_FIELD: "a", "source_quotes": ["b", "  "]}) == ["a", "b"]
    assert quotes_of({}) == []
    assert quotes_of({"source_quote": 123}) == []


# ---------------- 4. 报告与闸门 ----------------

def test_报告的分母口径():
    """`hit_rate` 不把 missing 计入分母 —— 否则"漏字段"会稀释真正的命中率。"""
    report = verify_payloads(
        [
            {QUOTE_FIELD: _LAW[:30]},        # exact
            {QUOTE_FIELD: _LAW[:30].replace("：", ":")},  # normalized
            {QUOTE_FIELD: "第八十七条 教师有权自行决定教学内容。"},  # fabricated
            {},                              # missing
        ],
        _CHUNKS,
    )
    assert report.n_exact == 1 and report.n_normalized == 1
    assert report.n_fabricated == 1 and report.n_missing == 1
    assert report.hit_rate == 0.6667  # 2 命中 / 3 有引用
    assert report.quote_coverage == 0.75  # 4 题里 3 题给了引用
    assert report.exact_ratio == 0.5  # 命中的 2 条里 1 条逐字


def test_没给引用的题在报告里必须看得见():
    """它是最该被盯住的信号：模型开始漏字段时，命中率会好看得毫无变化。"""
    report = verify_payloads([{"stem": "A"}, {"stem": "B", QUOTE_FIELD: _LAW[:30]}], _CHUNKS)
    assert report.n_missing == 1
    assert report.quote_coverage == 0.5
    assert report.hit_rate == 1.0  # ← 漏字段不稀释命中率，但 coverage 会掉下来


def test_闸门_编造必拦_未给引用默认放行():
    payloads = [
        {QUOTE_FIELD: _LAW[:30], "stem": "A"},
        {QUOTE_FIELD: "第八十七条 教师有权自行决定教学内容。", "stem": "B"},
        {"stem": "C"},
    ]
    kept, blocked, report = split_by_citation(payloads, _CHUNKS)
    assert [p["stem"] for p in kept] == ["A", "C"]
    assert [p["stem"] for p in blocked] == ["B"]
    assert report.blockable() == (1,)
    # 打开 require_quote 后，"没给引用"也要拦 —— 此时 C 也出局
    assert report.blockable(require_quote=True) == (1, 2)


def test_闸门开关关闭时原样返回(monkeypatch):
    from app.services.kb_generate import _apply_citation_gate

    monkeypatch.setattr(settings, "citation_gate_enabled", False)
    payloads = [{QUOTE_FIELD: "第八十七条 教师有权自行决定教学内容。", "stem": "B"}]
    assert _apply_citation_gate(payloads, _CHUNKS) == payloads


def test_闸门开启时拦截并把编造写进事件详情(monkeypatch):
    from app.services.kb_generate import _apply_citation_gate

    monkeypatch.setattr(settings, "citation_gate_enabled", True)
    monkeypatch.setattr(settings, "citation_require_quote", False)
    events = []
    payloads = [
        {QUOTE_FIELD: _LAW[:30], "stem": "A"},
        {QUOTE_FIELD: "第八十七条 教师有权自行决定教学内容。", "stem": "B"},
    ]
    kept = _apply_citation_gate(payloads, _CHUNKS, emit=lambda t, s, d=None: events.append((t, s, d)))
    assert [p["stem"] for p in kept] == ["A"]
    assert events and events[0][0] == "citation"
    # 详情必须带上被拦下的原文引用 —— 没有它就无法人工复盘"模型到底编了什么"
    assert events[0][2]["blocked"][0]["stem"] == "B"
    assert "第八十七条" in events[0][2]["blocked"][0]["quotes"][0]


def test_报告为不可变快照():
    report = verify_payloads([{QUOTE_FIELD: _LAW[:30]}], _CHUNKS)
    assert isinstance(report, CitationReport)
    assert report.as_row()["quotes"] == 1
    with pytest.raises(Exception):
        report.chunk_count = 999  # frozen dataclass：指标一旦产出不应被改写


# ---------------- 5. 成对指标 ----------------

def _ok(_quote, _chunks):
    return True


def _deny(_quote, _chunks):
    return False


def test_成对指标_只看流出编造率是自欺():
    """闸门从不拦、且候选里没有编造 → 流出编造率 = 0，但这个 0 **毫无信息量**。

    这正是 `sound` 要求 `拦截率 > 0` 的原因：否则一个"关掉的闸门"
    和一个"有效的闸门"在数字上完全一样。
    """
    from eval.metrics import evaluate_citation_gate

    m = evaluate_citation_gate(
        [{"quote": "第七条 教师享有下列权利", "chunks": _CHUNKS, "fabricated": False}], _ok
    )
    assert m.leaked_fabrication_rate == 0.0
    assert m.interception_rate == 0.0
    assert not m.sound  # ← 没有已知编造可拦，等于没有证据


def test_成对指标_拦得下且不漏放才算达标():
    from eval.metrics import evaluate_citation_gate

    cases = [
        {"quote": "第七条 教师享有下列权利", "chunks": _CHUNKS, "fabricated": False},
        {"quote": "第八十七条 教师有权自行决定教学内容。", "chunks": _CHUNKS, "fabricated": True},
    ]
    m = evaluate_citation_gate(
        cases, lambda q, ch: verify_quote(q, ch).ok
    )
    assert m.n_known_fabricated == 1 and m.n_intercepted == 1 and m.n_passed == 1
    assert m.interception_rate == 1.0
    assert m.leaked_fabrication_rate == 0.0
    assert m.false_negative_rate == 0.0
    assert m.sound


def test_成对指标_漏放会被计入流出编造率():
    from eval.metrics import evaluate_citation_gate

    cases = [{"quote": "编造的条文", "chunks": _CHUNKS, "fabricated": True}]
    m = evaluate_citation_gate(cases, _ok)
    assert m.leaked_fabrication_rate == 1.0
    assert not m.sound


# ---------------- 6. 假客户端必须给可校验的引用 ----------------

def test_假客户端摘录的引用可通过校验():
    """若 fake 给的是假引用，`LLM_MODE=fake` 下所有题都会被闸门拦掉 ——
    测试将再也覆盖不到"闸门之后"的链路（等于把下游一起测没了）。"""
    prompt = kb_question_prompt(_CHUNKS, "single", 2, scope="教师权利")
    text = FakeLLMClient().ask(prompt)
    items = json.loads(text)["questions"]
    assert items and all(QUOTE_FIELD in it for it in items)
    for it in items:
        assert verify_quote(it[QUOTE_FIELD], _CHUNKS).ok


def test_提示词要求引用必须原样摘录():
    """提示词不把"逐字"讲清楚，模型就会改写 —— 而改写过的引用在子串判定下等于编造。"""
    prompt = kb_question_prompt(_CHUNKS, "single", 1)
    assert "source_quote" in prompt and "原样摘录" in prompt


def test_无切片时_假客户端不给引用而不是给假引用():
    """没有切片可摘时返回 None（记为"未给引用"），**不能**编一个来充数。"""
    assert _quote_from_prompt("【生成题目】\n没有任何切片块") is None
    items = json.loads(FakeLLMClient().ask(kb_question_prompt([], "single", 1)))["questions"]
    assert items and QUOTE_FIELD not in items[0]
