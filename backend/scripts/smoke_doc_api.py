#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 出题链路一键冒烟测试（仅用标准库，无需安装任何依赖）。

用法：
    python scripts/smoke_doc_api.py                      # 用内置测试文本
    python scripts/smoke_doc_api.py 我的讲义.pdf          # 用你自己的文件
    python scripts/smoke_doc_api.py --base http://172.20.10.2:8000

前置：后端已启动（`python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`）
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

DEFAULT_BASE = "http://127.0.0.1:8000"

PASSED = []
FAILED = []


def report(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    mark = "[OK]  " if ok else "[FAIL]"
    line = f"{mark} {name}"
    if detail:
        line += f"  -- {detail}"
    print(line)


def req(method, path, base, headers=None, body=None, ctype=None, timeout=60):
    url = base.rstrip("/") + path
    r = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    if ctype:
        r.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


def multipart(field, filename, content_bytes, content_type="text/plain"):
    b = uuid.uuid4().hex
    head = (
        f"--{b}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{b}--\r\n".encode("utf-8")
    return head + content_bytes + tail, f"multipart/form-data; boundary={b}"


def demo_text():
    ch1 = "教育的本质是培养人的社会活动，这是教育区别于其他社会活动的根本特征。" * 40
    ch2 = "教学原则是根据教育目的和教学规律制定的指导教学工作的基本要求。" * 40
    return f"第一章 教育基础\n{ch1}\n第二章 教学原理\n{ch2}"


def main():
    args = [a for a in sys.argv[1:]]
    base = DEFAULT_BASE
    if "--base" in args:
        i = args.index("--base")
        base = args[i + 1]
        args = args[:i] + args[i + 2 :]

    print("=" * 60)
    print(f"AI 出题链路冒烟测试   后端：{base}")
    print("=" * 60)

    # 0) 健康检查
    st, body = req("GET", "/health", base, timeout=5)
    if st != 200:
        print(f"\n后端没在跑（{st}：{body}）。")
        print("请先在 backend 目录执行：")
        print("  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000")
        return 2
    report("0. 后端健康检查", True, str(body))

    # 1) 拿身份
    st, body = req("POST", "/api/identity/guest", base)
    if st != 200:
        report("1. 获取游客身份", False, f"{st} {body}")
        return 1
    uid = body.get("unionid")
    report("1. 获取游客身份", True, f"unionid={uid}")
    H = {"X-Unionid": uid}

    # 2) 上传
    if args and os.path.exists(args[0]):
        path = args[0]
        with open(path, "rb") as f:
            data = f.read()
        fname = os.path.basename(path)
    else:
        data = demo_text().encode("utf-8")
        fname = "测试讲义.txt"
        if args:
            print(f"（文件不存在，改用内置测试文本：{args[0]}）")

    body_bytes, ctype = multipart("file", fname, data)
    st, doc = req("POST", "/api/documents", base, headers=H, body=body_bytes, ctype=ctype)
    if st != 200:
        report("2. 上传并解析", False, f"{st} {doc}")
        print("\n若是 404 candidate not found：说明 unionid 失效，重跑本脚本即可。")
        return 1
    report(
        "2. 上传并解析",
        doc.get("chunk_count", 0) > 0 and doc.get("status") == "parsed",
        f"chunk_count={doc.get('chunk_count')} char_count={doc.get('char_count')} status={doc.get('status')}",
    )
    doc_id = doc["id"]

    # 3) 章节树
    st, detail = req("GET", f"/api/documents/{doc_id}", base, headers=H)
    headings = detail.get("headings", []) if st == 200 else []
    report("3. 章节树（知识点范围）", st == 200 and len(headings) > 0, f"headings={headings}")

    # 4) 提交生成
    payload = {
        "mode": "paper",
        "spec": [{"type": "single", "count": 4}, {"type": "blank", "count": 2}],
        "difficulty": "medium",
    }
    st, g = req(
        "POST",
        f"/api/documents/{doc_id}/generate",
        base,
        headers=H,
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ctype="application/json",
    )
    if st != 200:
        report("4. 提交生成", False, f"{st} {g}")
        return 1
    task_id = g["task_id"]
    report("4. 提交生成", True, f"task_id={task_id} total={g.get('total')}")

    # 5) 轮询
    st, t = 0, {}
    for _ in range(120):
        st, t = req("GET", f"/api/tasks/{task_id}", base, headers=H)
        if st == 200 and t.get("status") in ("done", "failed"):
            break
        time.sleep(0.2)
    want = g.get("total", 0)
    report(
        "5. 轮询任务进度（题数须与选择题量一致）",
        st == 200 and t.get("status") == "done" and t.get("question_count", 0) == want,
        f"status={t.get('status')} done={t.get('done')}/{t.get('total')} "
        f"题目数={t.get('question_count')}（应={want}） error={t.get('error')}",
    )

    # 6) 个人题库闯关
    st, sess = req(
        "POST",
        "/api/sessions/start",
        base,
        headers=H,
        body=json.dumps({"doc_id": doc_id, "question_count": 3}, ensure_ascii=False).encode("utf-8"),
        ctype="application/json",
    )
    qs = sess.get("questions", []) if st == 200 else []
    report(
        "6. 个人题库闯关",
        st == 200 and len(qs) == 3 and all(q.get("module") == "个人资料" for q in qs),
        f"题目数={len(qs)} module={qs[0].get('module') if qs else '-'}",
    )

    # 7) 不消耗额度（B3）
    st, quota = req("GET", "/api/quota", base, headers=H)
    report(
        "7. 个人题不消耗每日额度（B3）",
        st == 200 and quota.get("used_today") == 0,
        f"used_today={quota.get('used_today')} / {quota.get('free_daily_limit')}",
    )

    # 8) 删除级联（A5）
    st, dele = req("DELETE", f"/api/documents/{doc_id}", base, headers=H)
    report(
        "8. 删除资料级联移除个人题（A5）",
        st == 200 and dele.get("removed_questions", 0) > 0,
        f"removed_questions={dele.get('removed_questions')}",
    )

    # 9) 负例：不支持的类型
    body_bytes, ctype = multipart("file", "a.exe", b"x", "application/octet-stream")
    st, r = req("POST", "/api/documents", base, headers=H, body=body_bytes, ctype=ctype)
    report("9. 拒绝不支持的文件类型", st == 400, f"{st} {r}")

    # 10) 负例：空配比
    body_bytes, ctype = multipart("file", "测试讲义.txt", demo_text().encode("utf-8"))
    st, d2 = req("POST", "/api/documents", base, headers=H, body=body_bytes, ctype=ctype)
    if st == 200:
        st, r = req(
            "POST",
            f"/api/documents/{d2['id']}/generate",
            base,
            headers=H,
            body=json.dumps({"spec": []}, ensure_ascii=False).encode("utf-8"),
            ctype="application/json",
        )
        report("10. 拒绝空的题型配比", st == 400, f"{st} {r}")
        req("DELETE", f"/api/documents/{d2['id']}", base, headers=H)

    print("\n" + "=" * 60)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("失败项：" + "、".join(FAILED))
    print("=" * 60)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
