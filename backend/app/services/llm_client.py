"""接缝 B：大模型客户端封装（服务层唯一 AI 出口，Implementation 3）。

- FakeLLMClient：测试用假实现，不消耗任何 API 额度（测试决策 33/45）。
- RealLLMClient：OpenAI 兼容 Chat Completions 真实实现（LLM_MODE=real 启用）。
- variant 产物要求结构化 payload（进入管线前仍过结构化校验，不通过即弃）。
"""

import json
import re
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..config import settings
from .prompts_kb import kb_question_prompt


@dataclass
class GenerationRequest:
    kind: str  # "variant" | "review_paragraph"
    knowledge_point: str
    context: dict | None = None


@dataclass
class GenerationResult:
    text: str
    source: str = "realtime"
    payload: dict | None = field(default=None)  # variant 的结构化题目产物（单题）
    payloads: list[dict] | None = field(default=None)  # doc_question 的结构化题目列表


def _variant_payload(kp: str, module: str, variant_no: int) -> dict:
    """构造一份可通过结构化校验的变式题 payload（fake 实现用）。"""
    return {
        "module": module,
        "knowledge_point": kp,
        "stem": f"（实时变式{variant_no}）下列关于《{kp}》的表述，正确的是？",
        "options": [
            {"key": "A", "text": f"《{kp}》的正确表述（实时变式{variant_no}）"},
            {"key": "B", "text": f"《{kp}》的常见误解（实时变式{variant_no}）"},
            {"key": "C", "text": f"与《{kp}》无关的表述（实时变式{variant_no}）"},
            {"key": "D", "text": f"对《{kp}》的颠倒表述（实时变式{variant_no}）"},
        ],
        "answer": ["A"],
        "explanation": f"本题考查《{kp}》：正确选项 A 为该考点的核心要点表述。",
        "type": "single",
    }


class LLMClient(ABC):
    @abstractmethod
    def generate(self, req: GenerationRequest) -> GenerationResult: ...


class FakeLLMClient(LLMClient):
    _counter = 0

    def generate(self, req: GenerationRequest) -> GenerationResult:
        FakeLLMClient._counter += 1
        n = FakeLLMClient._counter
        if req.kind == "variant":
            module = (req.context or {}).get("module", "职业理念")
            return GenerationResult(
                text=f"[fake-variant] 基于考点《{req.knowledge_point}》的变式题",
                payload=_variant_payload(req.knowledge_point, module, n),
            )
        if req.kind == "doc_question":
            ctx = req.context or {}
            qtype = ctx.get("qtype", "single")
            count = int(ctx.get("count", 1) or 1)
            payloads = _fake_doc_questions("文档-考点", qtype, count)
            return GenerationResult(
                text=payloads[0].get("stem", "") if payloads else "",
                payloads=payloads,
            )
        if req.kind == "chat_pack":
            # #31/#32 fake 生成：自增编号保证多题不重复（纯 LLM 路线不再绑定官方题）
            ctx = req.context or {}
            n = FakeLLMClient._counter
            FakeLLMClient._counter += 1
            mods = ["职业理念", "职业道德", "教育法律法规", "文化素养", "基本能力"]
            return GenerationResult(
                text=f"[fake-pack] 第{n}题：请谈谈你的看法。",
                payload={
                    "open_question": f"（模拟面试·第{n}题）假设你在教学中遇到一个关于「素质教育」的真实情境，请谈谈你会如何理解和处理？",
                    "key_points": [
                        "准确说出核心概念的定义",
                        "能结合一个教学情境举例说明",
                        "能指出常见的理解误区",
                    ],
                    "module": mods[n % len(mods)],
                    "difficulty": ctx.get("difficulty", "medium"),
                },
            )
        if req.kind == "chat_grade":
            # #31 fake 评分：第一轮回 need_probe（驱动追问链路），之后终评；
            # 用户答案带「[offtopic]」前缀时返回 off_topic（覆盖离题分支测试）。
            ctx = req.context or {}
            content = str(ctx.get("content", ""))
            kps = ctx.get("key_points") or []
            if content.startswith("[offtopic]"):
                return GenerationResult(
                    text="[fake-grade] off_topic",
                    payload={"verdict": "off_topic", "hint": "请围绕刚才的问题作答哦"},
                )
            probes = int(ctx.get("probes_used", 0) or 0)
            if probes < 1:
                return GenerationResult(
                    text="[fake-grade] need_probe",
                    payload={
                        "verdict": "need_probe",
                        "probe_question": "能再展开说说你的依据吗？比如结合一个具体情境。",
                    },
                )
            half = max(1, len(kps) // 2)
            return GenerationResult(
                text="[fake-grade] final",
                payload={
                    "verdict": "final",
                    "hit": kps[:half],
                    "missed": kps[half:],
                    "wrong": [],
                    "score": 60 if half * 2 < len(kps) else 75,
                    "feedback": "答出了部分核心要点" if kps[half:] else "核心要点覆盖完整",
                    "suggestion": "建议补充遗漏要点，并结合情境展开" if kps[half:] else "表述可以更凝练",
                },
            )
        return GenerationResult(
            text=f"[fake-paragraph] 关于《{req.knowledge_point}》的个性化复盘段落。（AI 生成，仅供参考）"
        )

    def ask(self, prompt: str, timeout: int = 30) -> str | None:
        """离线确定性回应，按提示词标记分发（出题 / 相关性 / 自检 / 改写）。"""
        if "【生成题目】" in prompt:
            tm = re.search(r'type 固定为 "(\w+)"', prompt)
            qtype = tm.group(1) if tm else "single"
            cm = re.search(r"生成 (\d+) 道", prompt)
            count = int(cm.group(1)) if cm else 1
            items = _fake_doc_questions("个人资料-考点", qtype, count)
            return json.dumps({"questions": items}, ensure_ascii=False)
        if "【检索相关性评分】" in prompt:
            return '{"relevant": true, "score": 0.9}'
        if "【生成自检】" in prompt:
            return '{"passed": true, "score": 0.9, "issues": []}'
        if "【查询改写】" in prompt:
            # 原查询附在标记之后，直接回退，避免改写死循环
            return prompt.split("【查询改写】", 1)[1]
        return ""  # 兜底：空输出，上层按「无产出」降级


class RealLLMClient(LLMClient):
    """OpenAI 兼容 Chat Completions（真实供应商接线，LLM_MODE=real 启用）。"""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.llm_api_key

    def generate(self, req: GenerationRequest) -> GenerationResult:
        if req.kind == "variant":
            module = (req.context or {}).get("module", "职业理念")
            prompt = (
                f'请出一道教资《综合素质》{module}模块考点《{req.knowledge_point}》的单选变式题，'
                '只输出 JSON：{"module":..., "knowledge_point":..., "stem":..., '
                '"options":[{"key":"A","text":...},...], "answer":["A"], "explanation":..., "type":"single"}'
            )
            content = self._chat(prompt)
            if content is None:
                return GenerationResult(text="", payload=None)
            try:
                payload = json.loads(content[content.index("{") : content.rindex("}") + 1])
                return GenerationResult(text=str(payload.get("stem", "")), payload=payload)
            except Exception:
                return GenerationResult(text="", payload=None)

        if req.kind == "doc_question":
            ctx = req.context or {}
            prompt = kb_question_prompt(
                ctx.get("chunks", []),
                ctx.get("qtype", "single"),
                int(ctx.get("count", 1) or 1),
                ctx.get("difficulty", "medium"),
                ctx.get("focus"),
                ctx.get("existing_stems"),
                ctx.get("scope"),
            )
            content = self._chat(prompt)
            if content is None:
                return GenerationResult(text="", payloads=[])
            try:
                payloads = parse_doc_questions(content)
            except Exception:
                payloads = []
            return GenerationResult(
                text=payloads[0].get("stem", "") if payloads else "",
                payloads=payloads,
            )

        if req.kind == "chat_pack":
            # #32 纯 LLM 生成路线：现场出情境化面试题 + 评分要点（不再从官方题库包装）
            ctx = req.context or {}
            difficulty = ctx.get("difficulty", "medium")
            style = (
                "请设计一个具体真实的教学情境（有场景、有人物、有冲突），情境描述完再向候选人提问"
                if difficulty == "hard"
                else "提一个直指核心概念、但要用自然的面试口吻说出的问题，可以带一点简短背景"
            )
            avoid = ctx.get("avoid") or []
            prompt = (
                "你是一位有 15 年经验、说话温和但眼光毒辣的教师资格面试官，正在单独面试一位应聘者。\n"
                f"本场难度：{'进阶' if difficulty == 'hard' else '基础'}。出题要求：{style}。\n"
                "题目范围：教资《综合素质》五大模块（职业理念/职业道德/教育法律法规/文化素养/基本能力）中的任意考点，自由发挥，"
                "但要像真人面试官临场想到的问题，禁止出现「关于XX的正确表述是」这类试卷腔。\n"
                + (f"本场已问过的话题（必须换角度，不要重复）：{json.dumps(avoid, ensure_ascii=False)}\n" if avoid else "")
                + "同时为这道题提炼 3-4 个核心回答要点（评分踩点用，表述具体可判）。最后标注题目所属模块。\n"
                '只输出 JSON：{"open_question": "...", "key_points": ["...", "..."], '
                '"module": "五模块之一"}'
            )
            obj = self._chat_json(prompt, timeout=60)
            if obj is None or not obj.get("open_question") or not obj.get("key_points"):
                return GenerationResult(text="", payload=None)
            return GenerationResult(
                text=str(obj["open_question"]),
                payload={
                    "open_question": str(obj["open_question"]),
                    "key_points": [str(k) for k in obj["key_points"]][:4],
                    "module": str(obj.get("module", "") or ""),
                    "difficulty": difficulty,
                },
            )

        if req.kind == "chat_grade":
            # #31 评分链：要点覆盖判定 + 追问/终评一次调用返回（verdict 三态）
            ctx = req.context or {}
            persona = ctx.get("persona", "coach")
            persona_style = (
                "你是温和的面试教练，说话像真人：先接住对方的话（可引用他上一句的原话片段），"
                "先肯定答对的部分，再自然地指出缺口，语气鼓励但不回避问题"
                if persona == "coach"
                else "你是严格的面试考官：不寒暄、直奔要害，指出不足时直接引用对方原话里的漏洞"
            )
            history = json.dumps(ctx.get("history", []), ensure_ascii=False)
            prompt = (
                f'{persona_style}。\n'
                f'面试问题：{ctx.get("open_question", "")}\n'
                f'核心要点（评分踩点依据）：{json.dumps(ctx.get("key_points", []), ensure_ascii=False)}\n'
                f'本轮之前用户已回答但未获终评的内容（含追问轮次）：{history}\n'
                f'用户本轮回答：{ctx.get("content", "")}\n'
                f'已追问次数：{ctx.get("probes_used", 0)}（上限 2）\n'
                '对话纪律（真人感的关键）：\n'
                '- 追问时必须先回应用户刚才说的内容（引用他原话里的关键词），再自然地发问；禁止凭空抛问题\n'
                '- 所有文案用口语，禁止表格腔、禁止「首先/其次/综上所述」式公文腔\n'
                '评判规则：\n'
                '1. 若回答与问题完全无关，verdict="off_topic"，hint 用一句话友好提醒；\n'
                '2. 仅当回答为空泛套话、或完全未触及任何核心要点时才可 verdict="need_probe"'
                '（已追问次数<2），probe_question 针对最关键的缺口，且要先引用用户原话；'
                '只要回答触及了至少 1 个要点，就必须 verdict="final" 直接评分，不要为难用户；\n'
                '3. verdict="final"：score=要点覆盖率百分制整数，'
                'hit=已覆盖要点、missed=遗漏要点、wrong=事实性错误表述（可空数组），'
                'feedback=像教练口头点评一样的 2-3 句话（先说好的，再说缺的，口语化），'
                'suggestion=一句具体可操作的改进建议。\n'
                '只输出 JSON。'
            )
            obj = self._chat_json(prompt, timeout=60)
            if obj is None or obj.get("verdict") not in ("off_topic", "need_probe", "final"):
                return GenerationResult(text="", payload=None)
            return GenerationResult(text="[chat_grade]", payload=obj)

        content = self._chat(
            f"请为考点《{req.knowledge_point}》写一段 60 字内的个性化复盘鼓励段落，"
            f"结尾必须带「（AI 生成，仅供参考）」。背景数据：{json.dumps(req.context or {}, ensure_ascii=False)}"
        )
        return GenerationResult(text=content or "")

    def _chat_json(self, prompt: str, timeout: int = 60) -> dict | None:
        """#31 聊天链路专用：失败返回 None（路由层显式 503，绝不静默给分）。"""
        content = self._chat(prompt, timeout=timeout)
        if not content:
            return None
        try:
            s = content.index("{")
            e = content.rindex("}") + 1
            obj = json.loads(content[s:e])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    def _chat(self, prompt: str, timeout: int = 30) -> str | None:
        if not self.api_key:
            return None
        body = json.dumps(
            {
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": "你是教资《综合素质》出题与复盘助手，只输出 JSON 或纯文本。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.7,
            }
        ).encode("utf-8")
        try:
            req = urllib.request.Request(
                settings.llm_api_base.rstrip("/") + "/chat/completions",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except Exception:
            return None

    def ask(self, prompt: str, timeout: int = 30) -> str | None:
        return self._chat(prompt)


def get_llm_client() -> LLMClient:
    # 接缝 B 的唯一切换点：LLM_MODE=fake（默认/测试，不耗额度）| real。
    if settings.llm_mode == "real" and settings.llm_api_key:
        return RealLLMClient()
    return FakeLLMClient()


_FAKE_Q_IDX = 0  # 全局自增，保证跨多次调用生成的题目全局唯一（避免被 _persist_questions 去重丢弃）


def _fake_doc_questions(module: str, qtype: str, count: int) -> list[dict]:
    """构造可通过结构化校验的伪题目（kb_generate/kb_graph 测试与 FakeLLMClient 复用）。

    module 固定为「个人资料」，跳过官方模块的考点归属校验；按题型补齐 options/answer。
    题干 / 考点用全局自增下标，确保多次调用之间不重复（否则补偿轮被去重）。
    """
    global _FAKE_Q_IDX
    items: list[dict] = []
    for _ in range(count):
        i = _FAKE_Q_IDX
        _FAKE_Q_IDX += 1
        base = {
            "module": "个人资料",
            "knowledge_point": f"{module}-{i}",
            "stem": (
                f"（伪题{i}）下列关于《{module}》的表述，正确的是？"
                if qtype != "blank"
                else f"（伪填空{i}）___ 是培养人的社会活动。"
            ),
            "explanation": f"本题考查《{module}》核心要点。",
            "type": qtype,
        }
        if qtype in ("single", "multiple", "judge"):
            if qtype == "judge":
                base["options"] = [
                    {"key": "A", "text": f"{module} 正确表述{i}"},
                    {"key": "B", "text": f"{module} 错误表述{i}"},
                ]
                base["answer"] = ["A"]
            else:
                base["options"] = [
                    {"key": "A", "text": f"{module} 正确表述{i}"},
                    {"key": "B", "text": f"{module} 常见误解{i}"},
                    {"key": "C", "text": f"{module} 无关表述{i}"},
                    {"key": "D", "text": f"{module} 颠倒表述{i}"},
                ]
                base["answer"] = ["A"] if qtype == "single" else ["A", "B"]
        elif qtype == "blank":
            base["answer"] = [f"答案{i}"]
        else:  # short
            base["answer"] = f"参考答案文本{i}"
        items.append(base)
    return items


def parse_doc_questions(text: str) -> list[dict]:
    """解析知识库出题 LLM 输出为题目 dict 列表（与 prompts_kb._extract_json 同源）。

    期望输出形如 {"questions":[{...}]}（见 kb_question_prompt）；也兼容直接返回 JSON 数组。
    解析失败或非列表结构返回空列表（上层视为该批欠产，走补偿轮次或降级，不抛异常）。
    """
    if not text:
        return []
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    try:
        s = t.index("{")
        e = t.rindex("}") + 1
        obj = json.loads(t[s:e])
    except Exception:
        try:
            a = t.index("[")
            b = t.rindex("]") + 1
            obj = json.loads(t[a:b])
        except Exception:
            return []
    if isinstance(obj, list):
        items = obj
    elif isinstance(obj, dict):
        items = obj.get("questions") or []
    else:
        return []
    return [it for it in items if isinstance(it, dict)]
