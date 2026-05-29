"""接缝 B：大模型客户端封装（服务层唯一 AI 出口，Implementation 3）。

- FakeLLMClient：测试用假实现，不消耗任何 API 额度（测试决策 33/45）。
- RealLLMClient：OpenAI 兼容 Chat Completions 真实实现（LLM_MODE=real 启用）。
- variant 产物要求结构化 payload（进入管线前仍过结构化校验，不通过即弃）。
"""

import json
import re
import time
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
    #: 产出题目的**措辞是否多样**——即同考点出多道时，题干是否会明显不同。
    #:
    #: 判重的近似层（字符 n-gram Jaccard）依赖这个前提：真实模型换一道题会换一种说法，
    #: 而同模板实现只会改编号，彼此相似度约 0.8，近似判重会把它们成批误杀。
    #:
    #: 把它放在**客户端**而不是全局配置（`settings.llm_mode`）上是有意的：测试会在
    #: `LLM_MODE=real` 下注入 `FakeLLMClient`，挂在全局开关上就会漏判、把测试数据误杀干净。
    produces_varied_stems: bool = True

    @abstractmethod
    def generate(self, req: GenerationRequest) -> GenerationResult: ...


class FakeLLMClient(LLMClient):
    _counter = 0

    #: 同模板占位实现：题干固定为「（实时变式N）下列关于《考点》的表述，正确的是？」，
    #: 同考点内只差编号。故对其产物只做精确判重，不做近似/语义判重。
    produces_varied_stems = False

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
            items = _fake_doc_questions(
                "个人资料-考点", qtype, count, quote=_quote_from_prompt(prompt)
            )
            return json.dumps({"questions": items}, ensure_ascii=False)
        if "【主观题批改】" in prompt:
            return _fake_marking(prompt)
        if "【盲答投票】" in prompt:
            return _fake_blind_answer(prompt)
        if "【逐选项判定】" in prompt:
            return _fake_per_option(prompt)
        if "【采分点判定】" in prompt:
            return _fake_point_judge(prompt)
        if "【工具决策】" in prompt:
            return _fake_tool_decision(prompt)
        if "【答疑作答】" in prompt:
            return _fake_teacher_answer(prompt)
        if "【检索相关性评分】" in prompt:
            return '{"relevant": true, "score": 0.9}'
        if "【生成自检】" in prompt:
            return '{"passed": true, "score": 0.9, "issues": []}'
        if "【查询改写】" in prompt:
            # 原查询附在标记之后，直接回退，避免改写死循环
            return prompt.split("【查询改写】", 1)[1]
        return ""  # 兜底：空输出，上层按「无产出」降级


class LLMApiError(RuntimeError):
    """供应商返回非 2xx。**消息里带响应体** —— 见 `_describe_http_error`。"""

    def __init__(self, message: str, code: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.body = body

    @property
    def retryable(self) -> bool:
        """429（限流）与 5xx 值得重试；**其余 4xx 是已经说清楚了的拒绝**。

        实测代价：账户欠费时供应商回 400（`Arrearage`），而原来的重试逻辑把它当"瞬时失败"
        —— 主模型退避 2s+4s、再切备用通道重试 2 次。**这些等待不可能改变结果**，
        每次调用白花约 6 秒，在整个基准跑里累积成分钟级空转。分类之后明确拒绝直接跳出。
        """
        return self.code is None or self.code == 429 or self.code >= 500


def _describe_http_error(e) -> str:
    """把 `urllib` 的 HTTP 错误整理成**能自己说明原因**的一句话。

    为什么值得单独写：`HTTPError` 的 `str()` 只有 `HTTP Error 400: Bad Request` ——
    **响应体被丢掉了**，而供应商的 4xx 通常正是在响应体里说清原因
    （欠费 `Arrearage` / 模型名不存在 / 额度超限 / 参数非法）。
    实测代价：一次「账户欠费」被读成「请求写错了」，白查一轮。
    """
    body = ""
    try:
        if e.fp is not None:
            body = e.read().decode("utf-8", "replace")[:400]
    except Exception:  # noqa: BLE001 — 读不出来就算了；不能因为"想看清错误"而抛出新错误
        body = ""
    return f"HTTP {e.code} {e.reason}：{body or '(响应体为空)'}"


class RealLLMClient(LLMClient):
    """OpenAI 兼容 Chat Completions（真实供应商接线，LLM_MODE=real 启用）。"""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.llm_api_key
        # #36 备用供应商（可选）：主模型重试耗尽后自动切换，聊天链路不被单一供应商波动打断
        self.fallback = (
            (settings.llm_fallback_api_base, settings.llm_fallback_api_key, settings.llm_fallback_model)
            if settings.llm_fallback_api_base
            and settings.llm_fallback_api_key
            and settings.llm_fallback_model
            else None
        )
        #: 本进程内是否已放弃备用通道（它明确拒绝之后就没必要再试，见 LLMApiError.retryable）
        self._fallback_off = False

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
                f'已追问/提示轮次：{ctx.get("probes_used", 0)}（上限 {ctx.get("probe_limit", 4)}）\n'
                '对话纪律（真人感的关键）：\n'
                '- 追问时必须先回应用户刚才说的内容（引用他原话里的关键词），再自然地发问；禁止凭空抛问题\n'
                '- 所有文案用口语，禁止表格腔、禁止「首先/其次/综上所述」式公文腔\n'
                '评判规则：\n'
                '1. 若回答与问题完全无关（包括答的是上一题的内容），且补充轮次未达上限，'
                'verdict="off_topic"，hint 用一句话友好提醒并指明该往哪个方向答；已达上限时必须 verdict="final" 给低分；\n'
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
        """#36 主模型瞬时失败重试（退避 2s/4s ×2）；耗尽后切备用供应商（若配置）。

        **只重试值得重试的**：`LLMApiError.retryable` 为假时立即跳出 ——
        欠费 / 鉴权失败 / 参数非法这类拒绝，等 2 秒再问一遍得到的是同一个拒绝。
        """
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                return self._do_chat(prompt, timeout=timeout)
            except Exception as e:  # noqa: BLE001 — 重试耗尽后才降级
                last_err = e
                if isinstance(e, LLMApiError) and not e.retryable:
                    print(f"[llm] 主模型明确拒绝（{e.code}），不重试：{e}")
                    break
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))  # 2s / 4s
        print(f"[llm] 主模型重试耗尽: {type(last_err).__name__}: {last_err}")
        if self.fallback and not self._fallback_off:
            base, key, model = self.fallback
            for attempt in range(2):
                try:
                    print(f"[llm] 切换备用模型 {model}（第 {attempt + 1} 次）")
                    return self._do_chat(prompt, timeout=timeout, base=base, key=key, model=model)
                except LLMApiError as e:
                    last_err = e
                    if not e.retryable:
                        # 备用通道明确拒绝 → **本进程内不再试它**。否则后续每次调用
                        # 都要白等 2 次重试 + 2 秒退避（实测：备用欠费时每个调用都如此）。
                        self._fallback_off = True
                        print(f"[llm] 备用通道被拒绝（{e.code}），本进程内不再使用：{e}")
                        break
                except Exception as e:  # noqa: BLE001
                    last_err = e
                if attempt < 1:
                    time.sleep(2)
        print(f"[llm] 全部通道失败: {type(last_err).__name__}: {last_err}")
        return None

    def _do_chat(
        self,
        prompt: str,
        timeout: int = 30,
        base: str | None = None,
        key: str | None = None,
        model: str | None = None,
    ) -> str | None:
        use_base = base or settings.llm_api_base
        use_key = key or self.api_key
        use_model = model or settings.llm_model
        if not use_key:
            return None
        body = json.dumps(
            {
                "model": use_model,
                "messages": [
                    {"role": "system", "content": "你是教资《综合素质》出题与复盘助手，只输出 JSON 或纯文本。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.7,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            use_base.rstrip("/") + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {use_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 转成带**响应体**的错误再抛：否则上游只能看到 "HTTP Error 400: Bad Request"，
            # 而"欠费 / 模型名错 / 额度超限"这些真正的原因都在响应体里（见 _describe_http_error）
            raise LLMApiError(_describe_http_error(e), code=e.code) from e
        return data["choices"][0]["message"]["content"]

    def ask(self, prompt: str, timeout: int = 30) -> str | None:
        return self._chat(prompt)


def get_llm_client() -> LLMClient:
    # 接缝 B 的唯一切换点：LLM_MODE=fake（默认/测试，不耗额度）| real。
    if settings.llm_mode == "real" and settings.llm_api_key:
        return RealLLMClient()
    return FakeLLMClient()


_FAKE_Q_IDX = 0  # 全局自增，保证跨多次调用生成的题目全局唯一（避免被 _persist_questions 去重丢弃）

#: 提示词里的材料块头。**两种写法都要认**：
#: 出题链路是 `[切片 #12｜法名 / 章 / 条]`，问答老师的工具观察是 `[依据 #12｜…]`。
#: 只认一种会让另一半 fake 路径悄悄失去"可校验的引用"。
_BLOCK_HEADER_RE = re.compile(r"^\[(?:切片|依据) #(\d+)｜[^\]]*\]$")
#: 兼容旧名（模块内其它地方可能引用）
_CHUNK_HEADER_RE = _BLOCK_HEADER_RE


def _quote_from_prompt(prompt: str) -> str | None:
    """从出题提示词的切片块里**原样摘录**一句，作为 fake 的 `source_quote`。

    **为什么 fake 必须摘真实的原文，而不是编一个固定串**：引用闸门（`citation.py`）
    是硬校验 —— 若 fake 给的引用在任何切片里都定位不到，`LLM_MODE=fake` 下所有题都会被
    拦截，测试就再也覆盖不到闸门之后的链路（等于把闸门连同下游一起"测没了"）。
    fake 摘录真实原文，语义上等价于"真实模型引用正确"，闸门放行，链路照常被覆盖。

    摘取规则：取第一个切片块的首段（最多 40 字）。因为出题提示词是**逐行**追加切片正文的，
    按行还原后再截断，得到的是切片正文的真实前缀 —— 精确子串判定必然命中。
    """
    lines = prompt.splitlines()
    for i, line in enumerate(lines):
        if not _CHUNK_HEADER_RE.match(line.strip()):
            continue
        buf: list[str] = []
        for nxt in lines[i + 1:]:
            s = nxt.strip()
            # 空行 = 该切片正文结束；下一个切片头 / 输出格式行 = 强制结束
            if not s or _CHUNK_HEADER_RE.match(s) or s.startswith("输出格式"):
                break
            buf.append(nxt)
        text = "\n".join(buf).strip()
        if len(text) >= 12:
            return text[:40]
    return None


def _block_source(prompt: str) -> str:
    """从材料块头里取出处（`[依据 #12｜教师法 / 第二章 / 第七条]` → `教师法 / …`）。"""
    m = re.search(r"^\[(?:切片|依据) #\d+｜([^\]]*)\]$", prompt, re.M)
    return (m.group(1).strip() if m else "")


def _fake_tool_decision(prompt: str) -> str:
    """确定性工具决策：**没查过就查一次，查过就作答**。

    刻意只走"最少动作"：lookup_law / check_quote 的分支由单测直接构造提示词覆盖，
    这里保持链路短而稳定（工具循环本身另有专项用例）。
    不这么做的话，fake 模式下的问答会变成"每次都查一遍再答"，
    工具循环的轮次上限与"够不够"判据就测不出来了。
    """
    m = re.search(r"已用轮次：(\d+)/(\d+)", prompt)
    used = int(m.group(1)) if m else 0
    if used > 0 or "（还没有任何检索结果）" not in prompt:
        return json.dumps({"tool": "answer"}, ensure_ascii=False)
    q = re.search(r"用户问题：(.*)", prompt)
    return json.dumps(
        {
            "tool": "search_kb",
            "args": {"query": (q.group(1).strip() if q else "")},
            "reason": "先检索材料",
        },
        ensure_ascii=False,
    )


def _fake_teacher_answer(prompt: str) -> str:
    """确定性作答：**从观察块里原样摘录**作为引用。

    与出题链路同一个道理（见 `_quote_from_prompt`）：引用要能被 `citation.verify_quote`
    逐字命中，否则 fake 模式下每条回答都会被引用闸门拦成拒答 ——
    于是"有据答疑"这条链路在测试里永远走不到成功分支，等于没测。
    """
    quote = _quote_from_prompt(prompt)
    if not quote:
        return json.dumps(
            {
                "answer": "现有资料里没有能支撑这个问题的内容。",
                "citations": [],
                "confidence": "low",
                "insufficient": True,
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "answer": f"依据资料原文：{quote}",
            "citations": [{"quote": quote, "source": _block_source(prompt)}],
            "confidence": "high",
            "insufficient": False,
        },
        ensure_ascii=False,
    )


def _fake_marking(prompt: str) -> str:
    """确定性批改（主观题批改链路用）。

    与出题链路的引用校验同理：引用必须能被逐字命中，否则 fake 模式下
    **每次批改都会因"引用了依据里找不到的原文"而拒批** ——
    那条链路就再也测不到成功分支了。所以这里从依据块里原样摘一句。
    """
    quote = _quote_from_prompt(prompt)
    dims = {k: 72.0 for k in ("relevance", "evidence", "structure", "language")}
    return json.dumps(
        {
            "dimensions": dims,
            "comments": {k: "（替身评语，非真实批改）" for k in dims},
            "deductions": ["（替身）论据未结合材料"] if quote else [],
            "suggestions": ["（替身）先亮明理论点，再引材料佐证"],
            "citations": [{"quote": quote}] if quote else [],
        },
        ensure_ascii=False,
    )


def _fake_blind_answer(prompt: str) -> str:
    """确定性盲答（G3 唯一性投票用）：按 `_fake_doc_questions` 的字面约定挑正确选项。

    约定：正确选项的文本里带「正确表述」；多选题的答案是 `["A", "B"]`。
    fake 靠这个字面线索"答对"，用来模拟一个能独立推导出正确答案的模型 ——
    若它答不对，G3 会把 fake 模式下的**每一道题**都拦掉，那条链路就再也测不到了。
    （与 `_quote_from_prompt` 同一个道理：替身必须能走通成功分支，
    否则"有这道闸门"这件事本身就无法在离线环境里验证。）
    """
    if "可选多个" in prompt:
        return json.dumps({"answer": ["A", "B"]}, ensure_ascii=False)
    pairs = re.findall(r'"key"\s*:\s*"([A-Z])"\s*,\s*"text"\s*:\s*"([^"]*)"', prompt)
    good = [k for k, text in pairs if "正确表述" in text]
    return json.dumps({"answer": good or ["A"]}, ensure_ascii=False)


def _fake_per_option(prompt: str) -> str:
    """确定性逐项判定：文本含「正确表述」的选项判成立，其余判不成立。

    与 `_fake_blind_answer` **同一套字面约定** —— 替身必须能走通**成功分支**
    （判对的正好是答案键），否则"开着这道闸门会怎样"在离线环境里无从验证。
    约定一致还有个好处：fake 下 G3 与 G3' 的结论应当相同，可直接互相对照。
    """
    pairs = re.findall(r'"key"\s*:\s*"([A-Z])"\s*,\s*"text"\s*:\s*"([^"]*)"', prompt)
    verdicts = {k: ("正确表述" in text) for k, text in pairs}
    return json.dumps({"verdicts": verdicts}, ensure_ascii=False)


def _fake_point_judge(prompt: str) -> str:
    """确定性采分点判定：**全部算命中**。

    与 `_fake_blind_answer` / `_fake_per_option` 同一条约定 —— 替身必须能走通**成功分支**，
    否则"按采分点给分"这条链路在离线环境里无从验证（会恒为 0 分，所有用例都红）。
    """
    pts = re.findall(r"^\s*(\d+)\.\s*(.+)$", prompt, re.MULTILINE)
    hits = [{"point": int(i), "coverage": 1.0, "evidence": "（替身）覆盖该点"} for i, _ in pts]
    return json.dumps({"hits": hits}, ensure_ascii=False)


def _fake_doc_questions(module: str, qtype: str, count: int, quote: str | None = None) -> list[dict]:
    """构造可通过结构化校验的伪题目（kb_generate/kb_graph 测试与 FakeLLMClient 复用）。

    module 固定为「个人资料」，跳过官方模块的考点归属校验；按题型补齐 options/answer。
    题干 / 考点用全局自增下标，确保多次调用之间不重复（否则补偿轮被去重）。

    `quote`：写入 `source_quote` 的引用串。调用方（FakeLLMClient.ask）从提示词的
    切片块中真实摘录，使引用闸门可放行；为 None 时该字段缺省（闸门记为"未给引用"）。
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
        if quote:
            base["source_quote"] = quote
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
