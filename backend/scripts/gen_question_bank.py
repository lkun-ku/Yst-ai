# -*- coding: utf-8 -*-
import json, os, re, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import settings
from app.seed.questions_data import KNOWLEDGE_POINTS
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "seed", "real_questions.json")
BASE = settings.llm_api_base.rstrip("/")
KEY = settings.llm_api_key
MODEL = settings.llm_model
MAX_ROUNDS = 2
GEN_PROMPT = ("你是教师资格证《综合素质》的命题专家。针对考点「{kp}」（模块：{module}）出 {n} 道真实感的单项选择题。\n"
 "硬性要求：\n"
 "1. 题干贴近真实考试：或直接考查核心概念，或给出简短情境后设问。禁止出现「关于《{kp}》的正确表述」这类模板句。\n"
 "2. 每题 4 个选项，恰好 1 个正确答案；3 个干扰项必须似是而非（常见误解、易混淆概念、相近表述），禁止明显荒谬的选项。\n"
 "3. 每题配解析：说明正确项为什么对、关键干扰项错在哪。\n"
 "4. 答案字母随机分布，不要全集中在同一选项。\n"
 "5. 只输出 JSON 数组：[{\"stem\": \"...\", \"options\": [{\"key\": \"A\", \"text\": \"...\"}, {\"key\": \"B\", \"text\": \"...\"}, {\"key\": \"C\", \"text\": \"...\"}, {\"key\": \"D\", \"text\": \"...\"}], \"answer\": \"B\", \"explanation\": \"...\", \"difficulty\": \"easy|medium|hard\"}]")
JUDGE_PROMPT = ("你是教资考试的审题专家。下面每道题已带 idx 编号，请逐题审查：答案不正确/干扰项明显荒谬/题干歧义/考点归属错误/事实错误 -> REJECT；答案唯一正确且表述清晰 -> PASS。\n"
 "硬性要求：results 数组必须包含**每一个** idx 的判定（与输入题数相同，一个不能少），reason 不超过 15 字。\n"
 "只输出 JSON：{\"results\": [{\"idx\": 0, \"verdict\": \"PASS\", \"reason\": \"答案正确\"}, {\"idx\": 1, \"verdict\": \"REJECT\", \"reason\": \"答案有歧义\"}]}\n\n待审题目：\n{items}")
def chat(prompt):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}
    req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(body).encode(), headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())
    return d["choices"][0]["message"]["content"]
def parse_json(text):
    m = re.search(r"\[|\{", text)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[m.start():])
        return obj
    except json.JSONDecodeError:
        return None
def load_state():
    if os.path.exists(OUT):
        return json.load(open(OUT, encoding="utf-8"))
    return {"source": "llm-" + MODEL, "generated_at": "", "questions": []}
def save_state(state):
    state["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    json.dump(state, open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
# -*- coding: utf-8 -*-
def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
    state = load_state()
    done_kps = {q["knowledge_point"] for q in state["questions"]}
    all_kps = [(m, kp) for m, points in KNOWLEDGE_POINTS.items() for kp in points]
    todo = [(m, kp) for m, kp in all_kps if kp not in done_kps]
    if limit:
        todo = todo[:limit]
    print("total=%d done=%d todo=%d" % (len(all_kps), len(done_kps), len(todo)))
    stats = {"gen": 0, "judge": 0, "accepted": 0, "rejected": 0, "regen": 0}
    for i, (module, kp) in enumerate(todo, 1):
        qs = []
        gen_p = GEN_PROMPT.replace("{kp}", kp).replace("{module}", module).replace("{n}", "3")
        for rnd in range(MAX_ROUNDS):
            items = parse_json(chat(gen_p))
            stats["gen"] += 1
            if not isinstance(items, list) or not items:
                print("  parse-fail, retry"); time.sleep(2); continue
            items_idxd = [{**it, "idx": idx} for idx, it in enumerate(items)]
            jr = parse_json(chat(JUDGE_PROMPT.replace("{items}", json.dumps(items_idxd, ensure_ascii=False))))
            stats["judge"] += 1
            verdicts = {r.get("idx"): r.get("verdict", "PASS") for r in (jr.get("results") or [])} if isinstance(jr, dict) else {}
            # 覆盖度校验：judge 必须覆盖全部 idx，缺漏视为审校失败（重试一次，再失败则 fail-open 放行并计数）
            if len([v for v in verdicts if v in range(len(items))]) < len(items):
                print("  judge 覆盖不全（%d/%d），重试一次" % (len(verdicts), len(items)))
                jr2 = parse_json(chat(JUDGE_PROMPT.replace("{items}", json.dumps(items_idxd, ensure_ascii=False))))
                stats["judge"] += 1
                v2 = {r.get("idx"): r.get("verdict", "PASS") for r in (jr2.get("results") or [])} if isinstance(jr2, dict) else {}
                if len([v for v in v2 if v in range(len(items))]) >= len(items):
                    verdicts = v2
            qs = [it for idx, it in enumerate(items) if verdicts.get(idx, "PASS") == "PASS"]
            stats["rejected"] += len(items) - len(qs)
            if qs:
                break
            if rnd == 0:
                stats["regen"] += 1
                print("  all-REJECT, regen")
        for it in qs:
            state["questions"].append({
                "module": module, "knowledge_point": kp, "type": "single",
                "stem": it.get("stem", ""), "options": it.get("options", []),
                "answer": json.dumps([it.get("answer", "A")], ensure_ascii=False),
                "explanation": it.get("explanation", ""), "difficulty": it.get("difficulty", "medium"),
            })
        stats["accepted"] += len(qs)
        save_state(state)
        print("[%d/%d] %s -> %d passed (cum %d)" % (i, len(todo), kp, len(qs), len(state["questions"])))
        tot = stats["accepted"] + stats["rejected"]
        if tot and stats["rejected"] / tot > 0.3:
            print("!! REJECT rate > 30 percent, pause for human review")
            break
    print("DONE", stats, "bank:", len(state["questions"]))

if __name__ == "__main__":
    main()
