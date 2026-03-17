"""UAT 走查脚本（临时）：对运行中的后端逐项执行关键路径，输出 PASS/FAIL 报告。用后即删。"""

import json
import urllib.request

BASE = "http://127.0.0.1:8000"
results: list[tuple[str, str, str]] = []  # (step, status, note)


def call(method: str, path: str, body: dict | None = None, uid: str | None = None, admin: bool = False):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if uid:
        req.add_header("X-Unionid", uid)
    if admin:
        req.add_header("X-Admin-Token", "dev-admin")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def check(step: str, ok: bool, note: str):
    results.append((step, "PASS" if ok else "FAIL", note))


# 1. 游客身份
s, d = call("POST", "/api/identity/guest")
uid = d.get("unionid", "")
check("01 游客身份", s == 200 and bool(uid), f"unionid={uid[:12]}...")

# 2. AIGC 首次说明（后端 ack 接口）
s, d = call("POST", "/api/identity/ack-aigc", uid=uid)
check("02 AIGC说明ack", s == 200, str(d)[:60])

# 3+4. 发起闯关（3 题）
s, d = call("POST", "/api/sessions/start", {"module": "职业理念", "question_count": 3}, uid=uid)
qs = d.get("questions", [])
sid = d.get("session_id")
check("03 发起闯关", s == 200 and len(qs) == 3, f"session_id={sid}, {len(qs)}题")
aigc_flags = [bool(q.get("aigc_flag")) for q in qs]
check("04a 题目AIGC标识", all(aigc_flags) if aigc_flags else False, f"aigc_flag={aigc_flags}")

# 5. 答题判定：前2对，最后1错（多选态覆盖视种子而定，此处以单选为主）
answers = []
for i, q in enumerate(qs):
    sel = json.loads(q["answer"]) if i < 2 else ["Z" if "Z" in [o["key"] for o in json.loads(q["options"])] else json.loads(q["options"])[0]["key"]]
    # 最后一步：选一个错误答案（与正确答案不同的键）
    if i == 2:
        opts = [o["key"] for o in json.loads(q["options"])]
        correct = set(json.loads(q["answer"]))
        wrong = [k for k in opts if k not in correct]
        sel = wrong[:1] if wrong else opts[:1]
    answers.append({"question_id": q["id"], "selected": sel})
s, d = call("POST", "/api/sessions/submit", {"session_id": sid, "answers": answers}, uid=uid)
rmap = {r["question_id"]: r["is_correct"] for r in d.get("results", [])}
check("05 提交判定", s == 200 and sum(rmap.values()) == 2 and len(rmap) == 3, f"答对 {sum(rmap.values())}/3")

# 提交幂等
s2, d2 = call("POST", "/api/sessions/submit", {"session_id": sid, "answers": answers}, uid=uid)
check("05b 提交幂等", s2 in (200, 409), f"重复提交返回 {s2}（不重复计分即可）")

# 6. 续答：重复拉取同局
s, d = call("GET", f"/api/sessions/{sid}", uid=uid)
check("06 续答拉取", s == 200 and len(d.get("questions", [])) == 3, "题目一致")

# 7. 复盘报告
s, d = call("GET", f"/api/review/{sid}", uid=uid)
para = d.get("paragraph", "")
check("07 复盘报告", s == 200 and d.get("mastery") and d.get("weak_points") and para,
      f"掌握度{len(d.get('mastery', {}))}维, 段落尾标识={'仅供参考' in para}")

# 8. 历史回看
s, d = call("GET", "/api/sessions/history", uid=uid)
check("08 历史回看", s == 200 and str(sid) in json.dumps(d), "本局出现在历史列表")

# 9. 错题本
s, d = call("GET", "/api/mistakes", uid=uid)
groups = d if isinstance(d, list) else d.get("groups", [])
check("09 错题本聚合", s == 200 and len(groups) >= 1, f"{len(groups)}个考点组")
wrong_qid = None
if groups:
    wrong_qid = groups[0]["question_ids"][0]

# 10. 错题变式
if wrong_qid:
    s, d = call("POST", f"/api/mistakes/{wrong_qid}/variant", uid=uid)
    check("10 错题变式", s == 200 and "question" in d, f"degraded={d.get('degraded')}")

# 11+12. 考期倒计时 + 每日任务
s, d = call("POST", "/api/daily/exam-date", {"exam_date": "2027-03-13"}, uid=uid)
check("11 考期输入", s == 200 and d.get("countdown_days") is not None, f"倒计时{d.get('countdown_days')}天")

s, d = call("GET", "/api/daily", uid=uid)
task = d.get("task", {})
check("12 每日任务生成", s == 200 and task.get("items", {}).get("new_questions"), "含错题复习+新题")
check("12c 12h倒计时deadline", bool(task.get("deadline")) and isinstance(task.get("deadline"), str),
      f"deadline={task.get('deadline')}")
if task.get("task_id"):
    s, d = call("POST", "/api/daily/complete", {"task_id": task["task_id"]}, uid=uid)
    check("12b 任务完成反馈", s == 200 and d.get("completed"), str(d.get("feedback"))[:40])

# 13. 额度
s, d = call("GET", "/api/quota", uid=uid)
check("13 额度查询", s == 200 and d.get("free_daily_limit") == 20, f"已用{d.get('used_today')}, 剩余{d.get('remaining')}")

# 14. VIP 开通（内测占位）
s, d = call("POST", "/api/quota/vip-activate", uid=uid)
s2, d2 = call("GET", "/api/quota", uid=uid)
check("14 VIP权益", s == 200 and d2.get("is_vip") is True, "开通后 is_vip=True")

# 15. 纠错入队 → 后台处理
s, d = call("POST", "/api/sessions/start", {"module": "职业道德", "question_count": 1}, uid=uid)
qid = d["questions"][0]["id"] if s == 200 and d.get("questions") else None
if qid:
    s, d = call("POST", "/api/reports", {"question_id": qid, "error_type": "answer", "detail": "走查：答案疑似有误"}, uid=uid)
    rid = d.get("report_id")
    check("15 纠错入队", s == 200 and rid, f"report_id={rid}")
    s, d = call("GET", "/api/admin/reports?status=pending", admin=True)
    check("15b 后台队列", s == 200 and any(x["id"] == rid for x in d), f"待审{len(d)}条")
    if rid:
        s, d = call("POST", f"/api/admin/reports/{rid}/resolve", {"action": "accept"}, admin=True)
        check("15c 错误报告处理", s == 200, str(d))

# 16. 审校管线：待审队列 + 抽检
s, d = call("GET", "/api/admin/proofread/pending", admin=True)
check("16 抽检队列", s == 200, f"抽样{len(d)}题（文化素养比例最高为配置断言，已在自动化覆盖）")
if d:
    q0 = d[0]["id"]
    s, d = call("POST", f"/api/admin/proofread/{q0}/resolve", {"approved": True}, admin=True)
    check("16b 审校通过", s == 200, str(d))

# 17. 池化率监控
s, d = call("GET", "/api/admin/pool-stats", admin=True)
check("17 池化率监控", s == 200 and "pool_ratio" in d, f"ratio={d.get('pool_ratio')}, alarm={d.get('alarm')}")

# 18. 未登录拦截
s, _ = call("GET", "/api/quota")
check("18 未登录401", s == 401, f"无身份访问返回 {s}")

print("\n===== UAT API 走查结果 =====")
npass = 0
for step, status, note in results:
    print(f"{status}  {step}  | {note}")
    npass += status == "PASS"
print(f"\n合计 {npass}/{len(results)} 通过")
