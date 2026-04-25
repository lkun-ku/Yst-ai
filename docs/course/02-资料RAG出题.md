# 第 02 节 · 上传资料 RAG 出题

**对应代码**：`services/doc_parser.py`、`services/embedding.py`、`services/kb_retrieval.py`、`services/kb_generate.py`、`services/doc_generate.py`、`services/prompts_kb.py`、`routers/documents.py`

**这是三个功能里最复杂的一个，也是最有学习价值的。** 建议分两次读完。

## 学习目标

1. 说清 RAG 是什么、为什么能治幻觉
2. 理解嵌入向量的几何意义与余弦相似度
3. 看懂 numpy 矩阵化检索的每一行
4. 理解 RRF 为什么用排名而不是分数
5. 拆解出题「质量闭环」的八个环节，每环节解决什么问题
6. 知道「大卷 0 产出」那个 bug 的成因和解法

---

## 1. 为什么需要 RAG

### 先看反例

假设直接问模型：

> 「请根据《教育心理学》第三章出 5 道题。」

模型**根本没见过你的第三章**，只能凭训练数据里的印象编。结果是题干看起来很像样，
但细节全错 —— 这就是**幻觉（Hallucination）**。

### RAG 的思路

**RAG = Retrieval-Augmented Generation（检索增强生成）**

一句话：**在问模型之前，先把相关资料找出来，一起塞进 prompt。**

```
不 RAG：  问模型 ─────────────────> 模型凭记忆编 ──> 可能有幻觉
RAG   ：  找相关资料 ─┐
                      ├─> 一起给模型 ─> 模型「看着原文答」─> 可验证
          问模型 ─────┘
```

**【原理】RAG 不提高模型的智力，它改变的是模型的信息来源** ——
把「闭卷考试」变成「开卷考试」。

这也是为什么题目要求模型回传 `source_id`（依据哪一段）：
只有这样，幻觉才**可被检测**。

---

## 2. 全景：七个步骤

```
上传 PDF
   │
   ├─ ① 解析成纯文本              doc_parser.parse_document
   ├─ ② 切成 chunk（1500 字）      doc_parser.split_chunks
   ├─ ③ 每片算一个向量             embedding.embed_one     ← 异步，走 EMBED 线程池
   │
用户说「出 5 道单选题」
   │
   ├─ ④ 检索：找到最相关的 8 片     kb_retrieval.retrieve_by_scope
   ├─ ⑤ 拼 prompt（切片原文 + 要求） prompts_kb.kb_question_prompt
   ├─ ⑥ 模型出题（JSON）            llm_client
   └─ ⑦ 校验 → 去重 → 落库（带溯源）  validation + doc_generate._persist_questions
```

---

## 3. 步骤 ①②：解析与切片

### 为什么必须切片

| 原因 | 说明 |
|---|---|
| **上下文长度** | 模型一次能读的字有限。项目配的是 `doc_max_input_chars = 30000`，30 万字的教材塞不进去 |
| **检索精度** | 整本书算一个向量 = 「全书平均语义」，什么都检索不准 |

### 两级切分

```86:113:backend/app/services/doc_parser.py
def split_chunks(
    text: str | None,
    chunk_size: int = 1500,
    overlap: int = 200,
) -> list[dict]:
    """两级切分：结构切分（带 heading_path）+ 滑窗。

    返回 `[{"seq","content","heading_path","char_count"}]`。
    """
```

- **第一级：按标题切段** —— 用正则识别 `第X章`、`3.2`、`# 标题` 这类结构，给每片打上 `heading_path`
- **第二级：段内滑窗** —— 段太长时按 1500 字滑窗切开

### `overlap=200` 是干什么的

相邻两片有 200 字**重叠**：

```
chunk 1: [====================]              (0 ~ 1500)
chunk 2:            [====================]   (1300 ~ 2800)
                    ↑ 200 字重叠
```

如果一句话正好卡在第 1500 字和第 1501 字之间被切断，两片各拿半句，检索时都匹配不上。

**【原理】所有「分块处理」都要面对这个问题**：切分点不能是信息的断裂点。
视频分片、音频转写、流式解码都是同样的处理。

### ⚠️ 已知缺陷：页码信息丢失

```178:185:backend/app/services/doc_parser.py
    reader = PdfReader(path)
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")  # 单页解析失败不中断整体
    raw = "\n".join(pages)
```

`"\n".join(pages)` 把页与页拼成一大段 —— **页码信息在这里彻底丢失**。
所以现在无法告诉用户「这段在第 12 页」，也无法定位高亮。

（改造方向见第 06 节 P3：`parse_document` 按页保留，`DocumentChunk` 加
`page_no` / `char_start` / `char_end`。）

---

## 4. 步骤 ③：向量化 —— RAG 的数学基础

### 什么是嵌入（Embedding）

**把一段文字变成一串数字**：

```
"教育是有目的地培养人的社会活动"
        ↓ embedding 模型
[0.12, -0.87, 0.33, ..., 0.05]     ← 1024 个小数
```

**【原理】关键在于：语义相近的文字，向量在空间中距离近。**

这就是整个 RAG 的立足点 —— 把「语义相似度」这个模糊概念，
转化成「向量距离」这个可计算的量。

### 余弦相似度

```50:58:backend/app/services/embedding.py
def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
```

$$
\cos(\theta) = \frac{\vec{a} \cdot \vec{b}}{|\vec{a}|\,|\vec{b}|}
= \frac{\sum_i a_i b_i}{\sqrt{\sum_i a_i^2}\,\sqrt{\sum_i b_i^2}}
$$

**为什么用余弦而不是欧氏距离？**

余弦只看向量**方向**，忽略**长度**。文本向量有个特性：句子越长、信息越多，模长往往越大。
用欧氏距离的话，长句会「自动离得远」，这不合理。余弦把长度归一化掉，
只比较「说的是不是同一件事」。

**立刻可用的推论**：如果两边都归一化成单位长度（模长 = 1），
那么 $\cos(\theta)$ 就退化成点积 $\vec a \cdot \vec b$。

### 逐行读懂 numpy 矩阵化检索

```94:109:backend/app/services/kb_retrieval.py
def vector_rank(query_vec: list[float] | None, chunks: list[dict]) -> list[tuple[float, int]]:
    """向量通道：numpy 矩阵化余弦，返回 [(score, chunk_index)] 降序。

    与 `embedding.cosine_similarity` 语义一致，但以矩阵运算避免逐条 Python 循环。
    """
    usable = [(i, c["embedding"]) for i, c in enumerate(chunks) if c["has_vec"]]
    if not query_vec or not usable:
        return []
    idx = np.array([i for i, _ in usable], dtype=np.intp)
    mat = np.asarray([v for _, v in usable], dtype=np.float32)
    q = np.asarray(query_vec, dtype=np.float32)
    qn = q / (np.linalg.norm(q) + 1e-12)
    mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
    sims = mn @ qn  # (n,)
    order = np.argsort(-sims)
    return [(float(sims[j]), int(idx[j])) for j in order]
```

**① 筛选有向量的切片**

```python
usable = [(i, c["embedding"]) for i, c in enumerate(chunks) if c["has_vec"]]
```

`enumerate` 同时拿下标和值。这里只保留 `has_vec=True` 的（有些切片向量化失败）。

**② 记住「子集下标 → 原始下标」的映射**

```python
idx = np.array([i for i, _ in usable], dtype=np.intp)
```

⚠️ **这是本节最容易写出 bug 的地方。**

假设 5 个切片有向量，它们在原数组里的位置是第 `2, 7, 13, 40, 99` 个。
矩阵 `mat` 只有 5 行（下标 `0~4`）。如果不做映射直接返回 `0~4`，
就会把「第 7 个切片的内容」说成「第 2 个」—— **溯源全错**。

这个 bug 极难发现：**分数是对的，只有引用关系错了。**

> **写检索代码时永远问自己：我返回的索引，是哪个坐标系的下标？**

**③ 归一化与广播**

```python
mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
```

- `mat` 形状 `(5, 1024)`
- `np.linalg.norm(mat, axis=1)` → **按行**求模长，形状 `(5,)`
- `keepdims=True` → 保持成 `(5, 1)`
- `(5,1024) / (5,1)` → numpy 自动把 `(5,1)` **横向复制 1024 次**，逐元素相除

**【原理】广播（Broadcasting）**：numpy 在形状不匹配时自动扩展维度。
`keepdims=True` 就是为了让形状能对上。

如果不加：`(5,1024) / (5,)` → shape 对齐**从右往左**，会去匹配**列**，结果完全错。
**这个细节坑过无数人。**

**④ 矩阵乘法 = 批量余弦**

```python
sims = mn @ qn
```

`@` 是矩阵乘法。`(5,1024) @ (1024,)` → `(5,)`。
因为两边都已归一化成单位长度，结果**就是余弦相似度**。

**【原理】一次 BLAS 调用算出 5 个相似度，比 Python 循环快 10~100 倍。**
BLAS 底层是高度优化的 Fortran/C 汇编，能用 SIMD 指令并行。

**⑤ 降序排序**

```python
order = np.argsort(-sims)
```

`argsort` 返回的是**排序后的下标**而不是值。`-sims` 是为了降序
（numpy 的 `sort` 没有 `reverse=` 参数）。这个技巧要记住。

**⑥ `+ 1e-12`**

防止除零。**永远不要相信浮点数运算不会出现 0。**

---

## 5. 步骤 ④：检索 —— 三级降级与 RRF

### 三级降级

```190:231:backend/app/services/embedding.py
def retrieve(query, chunks, k=8, scope=None, embed_fn=None) -> list[dict]:
    """混合检索，带三级降级。"""
    if not chunks:
        return []
    pool = _filter_scope(chunks, scope) or chunks

    # 通道 1：向量检索
    ...
    if pairs:
        pairs.sort(key=lambda x: -x[0])
        return [c for _, c in pairs[:k]]

    # 通道 2：关键词检索
    scored = [(keyword_score(query, c.get("content") or ""), c) for c in pool]
    scored = [p for p in scored if p[0] > 0]
    if scored:
        scored.sort(key=lambda x: -x[0])
        return [c for _, c in scored[:k]]

    # 通道 3：均匀采样兜底
    return uniform_sample(pool, k)
```

| 级别 | 手段 | 什么时候用到 |
|---|---|---|
| 1 | 向量余弦 | 正常情况，语义最准 |
| 2 | 关键词命中 | 向量 API 挂了 / 还没算完 |
| 3 | 均匀采样 | 全挂了，至少保证覆盖全篇 |

**【原理】降级链（Fallback Chain）**：为每个可能失败的环节准备一个「更差但更可靠」的替代品，
目标是**永不返回空**。这属于 **fail-open**（失败就降级放行），
和评分链路的 **fail-close**（失败就报错）取向相反 —— 见第 03 节。

### 中文关键词为什么要用二元组

```151:166:backend/app/services/embedding.py
def _terms(query: str) -> list[str]:
    q = _PUNCT_RE.sub("", query or "")
    if not q:
        return []
    if len(q) < 2:
        return [q]
    # 中文用二元组，比单字更精确
    return [q[i : i + 2] for i in range(len(q) - 1)]


def keyword_score(query: str, content: str) -> int:
    terms = _terms(query)
    if not terms:
        return 0
    c = content or ""
    return sum(1 for t in terms if t in c)
```

英文按空格分词，中文没空格，所以用**二元组（bigram）**：

```
"教育基础" → ["教育", "育基", "基础"]
```

**为什么不用单字？** 拆成「教」「育」后，单字「教」会命中「教室」「教学」「教材」，
噪音太大。二字组合精确得多。

⚠️ 注释里提到过一个历史 bug：若按单字切分，`len(t) >= 2` 的过滤会**永远为空**
→ 关键词通道静默失效。**这类「静默失效」是最危险的 bug** ——
写过滤/检索逻辑时，务必给「结果为空」加断言或日志。

### RRF 融合：为什么不能直接加分数

```122:128:backend/app/services/kb_retrieval.py
def rrf(lists: list[list[tuple[float, int]]]) -> dict[int, float]:
    """Reciprocal Rank Fusion：多路召回融合为 chunk_index -> 融合分。"""
    fused: dict[int, float] = {}
    for lst in lists:
        for rank, (_score, ci) in enumerate(lst):
            fused[ci] = fused.get(ci, 0.0) + 1.0 / (_RRF_K + rank + 1)
    return fused
```

向量分数是 0~1 的小数，关键词命中数是整数（比如 7）。**这两个数不能相加** ——
量纲完全不同，加起来的「融合分」没有意义。

$$\text{RRF}(d) = \sum_{r \in R} \frac{1}{k + \text{rank}_r(d)}$$

**【原理】丢掉分数，只保留排名。** 排名是「序数」，天然可比。

`_RRF_K = 60` 的作用，用数字看最清楚：

| 排名 | $1/(60+\text{rank})$ |
|---|---|
| 第 1 名 | 0.01639 |
| 第 2 名 | 0.01613 |
| 第 10 名 | 0.01429 |

**第 1 名和第 2 名只差 1.6%。** 这个平滑的作用是：
**不奖励单通道的第一名太多，而是奖励「两路都上榜」**。

举例：

- 切片 X：向量第 1 名 → `0.0164`
- 切片 Y：向量第 3 名 + 关键词第 3 名 → `0.0159 + 0.0159 = 0.0317`

**Y 完胜 X。** 这正是我们要的：**被两种不同方式都认为是相关的，才是真的相关。**

### 标题加成：领域知识的补丁

```131:150:backend/app/services/kb_retrieval.py
def heading_bonus(scope: str, chunks: list[dict]) -> dict[int, float]:
    """标题路径精确命中加成：scope 术语命中 chunk 的 heading_path 时加分（上限 0.5）。
    ...
        hit = sum(1 for t in terms if t and t in hp)
        if hit:
            bonus[i] = min(hit * 0.1, 0.5)
```

向量检索是「模糊的语义匹配」，而章节标题是「精确的强信号」。
用户搜「教师权利」，若某切片的 `heading_path` 真的含「教师权利」，几乎可确定相关 —— 直接加分。

**「通用方法 + 领域补丁」的标准套路**：通用方法解决 80%，剩下 20% 用领域知识修。

### 均匀采样兜底

```171:178:backend/app/services/embedding.py
def uniform_sample(pool: list, k: int) -> list:
    """保序均匀抽样：保证覆盖首尾，避免只命中文档开头。"""
    if not pool:
        return []
    if k >= len(pool):
        return list(pool)
    step = len(pool) / k
    return [pool[int(i * step)] for i in range(k)]
```

**为什么不是取前 k 个？** 文档有结构（第 1 章在前、第 10 章在后）。
只取前 k 会让出的题全来自开头几章，用户会以为「这本资料只有前面有内容」。

**【本项目】同一招用了三次**：`uniform_sample`（检索兜底）、
`_trim_to_budget`（上下文裁剪）、`allocate_quota`（章节配额）。
**学一个模式，到处用** —— 这是好代码的特征。

### PG 路径：三个知识点

```168:223:backend/app/services/kb_retrieval.py
    vec_literal = "[" + ",".join(f"{x:.7f}" for x in qv) + "]"
    # 注意：必须用 CAST(:q AS vector) 而非 :q::vector —— SQLAlchemy 的 text() 会把
    # `::` 视为转义/转换符，导致 :q 不被识别为绑定参数而静默丢失（参数里只剩 cid/k，
    # 运行时报 "could not determine data type of parameter"）。
    sql = sa_text("""
        SELECT dc.id, ..., 1 - (dc.embedding <=> CAST(:q AS vector)) AS sim
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE d.candidate_id = :cid AND dc.embedding IS NOT NULL
        ORDER BY dc.embedding <=> CAST(:q AS vector)
        LIMIT :k
    """)
```

1. **`<=>` 是「余弦距离」不是相似度**：距离 = 1 − 相似度，所以代码写了 `1 - (...)`。
   而且**距离越小越相似，`ORDER BY` 是升序**；内存路径按分数降序。
   **两条路径排序方向相反**，必须对齐语义。
2. **`CAST(:q AS vector)` 不能写成 `:q::vector`**：SQLAlchemy 的 `text()` 把 `::` 当转义符，
   导致参数绑定失效。这是具体框架的坑。
3. **手工拼向量字面量**：`f"{x:.7f}"` 控制精度 —— 1024 维全精度会让 SQL 语句长到爆。

---

## 6. 步骤 ⑤⑥：生成

### prompt 的结构

```86:106:backend/app/services/prompts_kb.py
    lines = [
        f"你是个人知识库出题助手。请依据下方资料切片，生成 {count} 道{TYPE_DESC.get(qtype, TYPE_DESC['single'])}。",
        "",
        "硬性约束：",
        "1. 只能使用资料切片中出现的信息，禁止引入资料外的知识；资料未涉及的内容不要出。",
        f"2. 难度要求：{DIFF_DESC.get(difficulty, DIFF_DESC['medium'])}。",
        "3. 只输出 JSON，不要任何解释文字或代码块围栏。",
        "4. 每道题必须包含字段：module, knowledge_point, stem, explanation, type。",
    ]
```

**(a) 第 1 条是防幻觉的核心约束**：「禁止引入资料外的知识」。
没有这句，模型会把训练知识混进来，溯源就失效了。

**(b) 要求模型自报依据**：

```110:113:backend/app/services/prompts_kb.py
    lines.append(
        f"{idx}. 每道题额外输出字段 source_id：该题所依据的资料切片编号，"
        "取自下方 [切片 #N] 的 N（整数）。若依据多个切片，填最主要的那一个。"
    )
```

这是**溯源 + 防幻觉校验**的基础。**让模型自己说出依据，才能验证它有没有瞎编。**

**(c) 认知层级显式约束**：

```121:126:backend/app/services/prompts_kb.py
    if bloom_mix:
        # 认知层级显式约束：不指定时模型倾向全出记忆题（#26 A）
        lines.append(f"{idx}. 认知层级分布（布鲁姆）——严格按此比例出题，不要全部出成记忆复述题：")
        for lv, n in bloom_mix.items():
            lines.append(f"   - {n} 道：{BLOOM_DESC.get(lv, lv)}")
```

**【原理】布鲁姆认知层级（Bloom's Taxonomy）**：
记忆 → 理解 → 应用 → 分析 → 评价 → 创造。越往后认知要求越高。

不约束的话，模型默认约 **62%** 出「记忆题」（XXX 是什么），对备考价值最低（背就完了）。
显式要求「理解×2、应用×1」能显著提升题目质量。

**【原理】这是「用约束替代请求」**。写「请出高质量的题」没用，
写「必须包含 1 道应用层级、1 道分析层级」才可执行、可校验。

### 分批与降粒度重试

```209:216:backend/app/services/kb_generate.py
    sizes: list[int] = []
    # 单次生成不超过 doc_batch_size：大批次结构化输出失败率显著上升（#26）
    n = min(int(count), settings.doc_batch_size)
    while n > 1:
        sizes.append(n)
        n = (n + 1) // 2  # 向上取整：粒度序列连续（3→2→1），不跳级
    sizes.append(1)
    sizes = list(dict.fromkeys(sizes))  # 去重保序
```

让模型一次输出 3 道结构化 JSON 题，失败率很高（漏字段、格式崩、只输出 1 道）。
策略是「先试 3 道，不行试 2 道，再不行试 1 道」—— `3 → 2 → 1`。

`(n + 1) // 2` 是**向上取整的整除**：3→2、2→1。用「向上」而不是「向下」，
是为了让粒度序列**连续**（不跳级）。如果是 5：5→3→2→1。

**【原理】批量操作失败率高时，二分降级重试。** 这个套路在限流重试、
批量写入、分页拉取里都通用。

为什么是 3 而不是 6？看配置注释：

```56:58:backend/app/config.py
    # 每批题数：由 6 下调为 3——实测一次生成 6 题时模型常只输出 3 题（提示词含认知层级后更明显），
    # 反而要靠多轮补偿补齐，总耗时更高（133s vs 3 题批约 50s）；小批量成功率更高、总量更可控（#26 A）。
    doc_batch_size: int = int(os.getenv("DOC_BATCH_SIZE", "3"))
```

**注意注释里有真实测量数据（133s vs 50s）。** 用数据调参数，而不是凭感觉。

---

## 7. 质量闭环：八个环节

```
① 跨文档召回          retrieve_by_scope（RRF + 三级降级）
② 相关性评分          _grade_and_filter     ← LLM as Judge
③ 查询改写重检索      _rewrite_scope        ← 限 1 次
④ 拼上下文（预算内）   _trim_to_budget
⑤ 分批生成 + 自检      _generate_batch_with_fallback
⑥ 规则校验            validate_question_payload
⑦ 降粒度重试          3 → 2 → 1
⑧ 补偿轮              最多 5 轮，带余量生成 + 按需截断
```

### ② 为什么向量检索完还要再打分

```81:93:backend/app/services/kb_generate.py
def _grade_and_filter(client, scope: str, chunks: list[dict]) -> tuple[list[dict], bool]:
    """对召回切片做相关性评分并过滤；LLM 不可用时保守返回全部（退化）。"""
    kept: list[dict] = []
    for c in chunks:
        text = client.ask(relevance_grade_prompt(scope, c.get("content", "")))
        if text is None:
            return chunks, True  # 降级：保留全部
        relevant, score = parse_relevance(text)
        nc = dict(c)
        nc["relevance"] = score
        if relevant and score >= RELEVANCE_THRESHOLD:
            kept.append(nc)
    return kept, len(kept) >= max(1, len(chunks) // 2)
```

向量检索是「相关」的**近似**，不是判定。Top-16 里必然混着不相关的，
全塞进 prompt 会让模型被噪音带偏。

**【原理】LLM as Judge（大模型当评判）**。三个设计细节：

| 细节 | 代码 | 作用 |
|---|---|---|
| 阈值 | `RELEVANCE_THRESHOLD = 0.5` | 不是「模型说相关就留」，还要分数够高 |
| 充分性判据 | `len(kept) >= max(1, len(chunks) // 2)` | 保留超过一半才算「够用」 |
| 降级 | `text is None` → 保留全部 | fail-open，绝不因评分挂了就无法出题 |

注意 `nc = dict(c)` —— **浅拷贝**，避免修改原字典（原始召回结果还被后续环节使用）。

### ③ 查询改写：为什么限 1 次

```339:351:backend/app/services/kb_generate.py
    if enable_loop:
        graded, sufficient = _grade_and_filter(client, scope, chunks)
        rewrites = 0
        while not sufficient and rewrites < MAX_REWRITE:
            new_scope = _rewrite_scope(client, scope, "相关切片不足")
            if new_scope == scope:
                break
            emit("rewrite", f"查询改写：「{scope}」→「{new_scope}」")
            scope = new_scope
            chunks = retrieve_by_scope(db, candidate_id, scope, k=KB_RECALL_K, embed_fn=embed_fn)
            graded, sufficient = _grade_and_filter(client, scope, chunks)
            rewrites += 1
```

**为什么 `MAX_REWRITE = 1`？**

1. **成本**：每轮 = 1 次 LLM 调用 + 1 次检索 + N 次评分，放开会指数放大（注释写「~1.5~2x」）
2. **收敛性**：改写是自反馈循环，**没有任何保证它会更准**，可能越改越偏

`if new_scope == scope: break` —— 改写没变化就退出，**防止死循环**。

**【原理】所有自反馈循环的通用防御：限制轮次 + 检测无进展。**
项目里的 `MAX_REGEN = 1`、补偿轮 `attempts < 5`、`MAX_PROBES = 2` 都是同一招。

### ⑤ 自检：抽检升级机制

```132:169:backend/app/services/kb_generate.py
def _selfcheck_batch(client, payloads, chunks, sample=None, ctx_limit=None):
    """自检：LLM 不可用放行；支持抽检，抽检发现问题再升级为全批检（#26）。
    ...
    """
    ...
    checked = targets
    if sample and 0 < sample < len(targets):
        # 均匀取样（覆盖首尾），避免只检前几题
        step = len(targets) / sample
        idxs = sorted({min(len(targets) - 1, int(i * step)) for i in range(sample)})
        checked = [targets[i] for i in idxs]

    # 单趟遍历收集结果：避免在「发现不通过」后再对同一批重复调用一次（#26 P1）
    keep: list[dict] = []
    all_ok = True
    for p in checked:
        if _ask_selfcheck(client, p, chunks, limit) is False:
            all_ok = False
        else:
            keep.append(p)

    if all_ok:
        return list(targets), True
    if len(checked) < len(targets):
        # 抽检发现问题 → 升级为全批检（递归一次，全量遍历，不重复检测已检项）
        return _selfcheck_batch(client, targets, chunks, sample=None, ctx_limit=limit)
    return keep, False
```

**这是本节最值得学的设计。** 它解决「质量检查成本 vs 覆盖」的矛盾：

```
全批检：每批都调 LLM → 贵，但准
纯抽检：只检 2 题    → 便宜，但漏检 1 题就流到用户手里
```

**两级递进**：

1. 先抽检 2 题
2. 全部通过 → 视为整批合格，只花 2 次调用
3. **发现 1 个不合格** → 这批质量存疑 → **升级为全批检**

**【原理】统计学里的「验收抽样（Acceptance Sampling）」**：
低成本快速放行好批次，可疑批次才付全价检查。

还有个性能细节（注释写明）：**单趟遍历收集结果**。
朴素写法是「先遍历看有没有不合格，再遍历收集合格的」→ 不合格时白跑一遍。

### 那个「大卷 0 产出」的 bug —— 最有价值的一课

```103:115:backend/app/services/kb_generate.py
def _ctx_for_question(p: dict, chunks: list[dict], limit: int) -> str:
    """自检依据：优先取该题**自己溯源到的切片**（#26）。

    此前把 `chunks[:6]` 拼接后再截 800 字，而生成侧可看 `doc_max_input_chars=30000`
    字——依据后段切片出的题在自检时看不到原文，会被必然判为「无依据」而误杀，
    这是大卷（8 题）0 产出的直接根因。
    """
    sid = p.get("source_id")
    if sid is not None:
        hit = next((c for c in chunks if str(c.get("id")) == str(sid)), None)
        if hit:
            return (hit.get("content") or "")[:limit]
    return "\n".join(c.get("content", "") for c in chunks[:6])[:limit]
```

**完整因果链**：

```
1. 生成时：模型能看到 30000 字上下文
2. 自检时：只把 chunks[:6] 截 800 字给审查员
3. 结果：依据第 7 片之后的内容出的题，审查员根本看不到那段原文
4. 审查员只能判「无依据」→ 判不合格
5. 所有题都被判不合格 → 大卷产出 0
```

**【原理】当 A 环节产出交给 B 环节审核时，B 必须能看到 A 所依据的全部证据。**
否则 B 的否决是**噪音，不是信号**。

修复方案也很漂亮：生成时让模型自报 `source_id`，
自检时**只喂那道题自己的依据切片** —— 既公平（能看到原文），又省 token（1 片而非 6 片）。

### ⑥⑦ 「自检不该造成欠产」

```253:274:backend/app/services/kb_generate.py
        if not selfcheck:
            # 路线①：只做规则校验 + 降粒度重试，不额外发起自检调用
            accepted += ok_payloads
            continue

        passed, all_ok = _selfcheck_batch(client, ok_payloads, chunks, sample=sample)
        if emit:
            emit(
                "selfcheck",
                "自检 · %d/%d 通过%s"
                % (len(passed), len(ok_payloads), "（全部通过）" if all_ok else "（保留规则通过项）"),
            )
        accepted += passed

    # 兜底：自检若把所有题都否决（例如模型评判偏严），仍保留规则校验通过项。
    # 自检用于标记风险，不应成为欠产的原因——这正是本次修复的核心语义（#26）。
    if not accepted and last_ok:
        if emit:
            emit("selfcheck", "自检全部未通过，降级保留规则校验通过项（风险已记录）")
        return last_ok[:count]
```

**这段注释里的决策哲学值得抄下来**：自检是**风险标记**，不是**准入闸门**。

| 设计 | 后果 |
|---|---|
| 旧：自检不过 → 整批归零 | 严格，但欠产（用户拿不到题） |
| 新：自检不过 → 降级保留（风险已记录） | 可能有一个坏题，但用户有题做 |

哪个对？**取决于失配的代价**。这里判断「没题做」比「偶尔一道题质量差」更伤留存。
这个判断不一定普适 —— 但**把判断写进注释**这个习惯是无价的。

### ⑧ 补偿轮：带余量生成 + 按需截断

```53:61:backend/app/services/kb_generate.py
def compensation_count(need: int) -> int:
    """补偿轮生成题数：带余量、有上限（#23 出题欠产修复）。

    此前用 `min(doc_batch_size * 3, need)`，当 need=1 时只生成 1 道题——
    该题一旦因与已有题干重复被 seen 去重、或未通过结构化校验，本轮即完全空转，
    5 轮耗尽后仍永久欠产。故保证下限 doc_batch_size（缺口小也有候选），
    上限 doc_batch_size * 3（控制 token 成本）；超产由 _persist_questions(limit=) 截断。
    """
    return min(max(need * 2, settings.doc_batch_size), settings.doc_batch_size * 3)
```

**这是「批次损耗」的经典解法**：

```
缺口 = 1 → 按需生成 1 道 → 这 1 道被去重/校验拦掉 → 本轮净产出 0
         → 5 轮后还是缺 1 道
```

修法：**永远多生成一些**（`need * 2`，且至少一个批次），超产部分靠 `limit` 截断。

```
need = max(need*2, 3)   下限 3
     ≤ 3 * 3 = 9        上限 9
```

**【原理】生产线上必须有安全库存。** 只不过这里用在 LLM 出题上。

---

## 8. 去重与溯源

### 两层去重

```python
# backend/app/services/doc_generate.py _persist_questions 中的顺序
for p in payloads:
    errors = validate_question_payload(p, set())
    if errors: continue                          # 闸门 1：结构校验
    key = normalize_stem(p.get("stem"))
    if key and key in seen: continue             # 闸门 2：精确去重（归一词干）
    # 闸门 3：近似判重（字符 3-gram Jaccard）
    if jaccard >= threshold: continue
    # ... 落库
    if limit is not None and len(created) >= limit: break   # 最后才截断
```

**为什么需要两层？** 模型会输出「XXX 的正确表述是？」和「XXX 的正确表述是 ？」（多一个空格），
精确比对拦不住。近似判重用**字符 3-gram Jaccard 相似度**：
把句子切成 3 字一组，算两个集合的重合比例。

$$
J(A, B) = \frac{|A \cap B|}{|A \cup B|}
$$

阈值 0.6 也是实测定的：

```69:72:backend/app/config.py
    # 题干近似判重阈值（#26 遗留 3）：实测真实语义重复的 Jaccard 约 0.625，不同考点题目通常 <0.4，
    # 故 0.6 能有效拦截且不会误杀。fake 模式整体跳过判重（见 doc_generate._persist_questions）——
    # 同模板伪题相似度约 0.8，不跳过会误杀（实测 12 题只剩 3 题）。
    doc_stem_dup_threshold: float = float(os.getenv("DOC_STEM_DUP_THRESHOLD", "0.6"))
```

**阈值不是拍脑袋，是量出来的**：重复题 0.625 > 0.6（拦住），不同考点 < 0.4（放行）。

### ⚠️ `limit` 的时序很关键

`limit` 必须在**校验和去重之后**执行。如果先截断，会把「被丢弃的题」也算进配额，
导致实际产出少于预期。

**【原理】配额/计数要作用在「有效产出」上，不是「尝试次数」上。**

### 溯源：白名单校验防幻觉

```154:162:backend/app/services/doc_generate.py
def picked_ids_of(picked: list[dict] | None) -> set[int]:
    """本次召回切片的 id 集合。

    工单 20/W-6：用于校验模型回传的 `source_id` 是否真实存在于本次召回切片中，
    防止模型幻觉出不存在的编号导致溯源指向错误。
    """
    if not picked:
        return set()
    return {c["id"] for c in picked if c["id"] is not None}
```

模型回传 `source_id: 37`，但 37 可能是它编的。所以拿本次真正召回的 id 集合做**白名单校验** ——
不在集合里就不采信，回退到「批级溯源」（记录整批切片 id）。

**【原理】** 不要让模型自由输出，而是**给它一个有限的候选集**，输出后校验是否落在集合内。
这是防幻觉的通用手段（受限生成的工程近似版）。

对后续要做的「问答老师」**直接适用**：让模型输出引用编号 → 校验在召回集合内 →
不在就丢弃或标注「未找到依据」。

---

## 9. 异步与并发

### 为什么向量化要异步

上传接口要快速返回（用户不能等 30 秒）。所以向量化丢到后台线程：

```262:263:backend/app/routers/documents.py
    # #26 P2：向量化走独立池，避免与出题任务互抢 worker 造成互等
    submit(_embed_document, doc.id, pool=EMBED)
```

### 那个必须 `rollback` 的等待

```108:146:backend/app/services/embedding.py
def wait_embed_ready(db, *, document_id=None, candidate_id=None, timeout=20.0, interval=0.5) -> bool:
    """等待切片向量化完成——供出题任务在检索之前调用（#23）。

    上传接口为保响应速度把 embedding 放到后台线程；出题任务若在其完成前检索，
    `vector_rank` 的 usable 为空 → `vector_score` 恒为 0 → 静默退化为纯关键词检索，
    出题质量下降且调用方完全无感知。实测：上传后不等待即检索 vector_score=0，
    等待后为 0.82。
    ...
    """
    ...
    deadline = time.time() + timeout
    while True:
        # rollback 结束当前事务快照，否则读不到 embed 线程已提交的新状态（会一直等到超时）
        db.rollback()
        if _pending_count() == 0:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(interval)
```

**为什么必须 rollback？** 因为数据库默认隔离级别下，事务一旦开始，
**后续所有读取都看到事务开始那一刻的快照**（MVCC）。

```
t0: 出题线程开始事务，快照 = {embed_status: pending}
t1: embed 线程更新 embed_status = ok 并提交
t2: 出题线程再查 → 仍看到 t0 的快照 → pending！
t3: 永远等不到 → 超时
```

`db.rollback()` 结束当前事务并开启新事务 → 拿到新快照 → 看到别人提交的数据。

**【原理】这是极高频的并发 bug 模式：「轮询看不到另一个线程/进程的写入」。**
写任何「等待另一个执行体完成」的代码，都要问自己：**我的读取会不会看到陈旧快照？**

### 协作式取消

出题任务支持用户中途停止，但**不在任意时刻强杀**，而是在「批次边界」检查：

```269:272:backend/app/services/doc_generate.py
    `should_stop()`：协作式取消回调（#26）。在**批次之间**调用，返回 True 时停止后续批次
    并**返回已生成的题目**——这样取消后仍能得到部分卷，而不是丢弃全部结果。
```

**【原理】协作式取消（Cooperative Cancellation）**：被取消方自己检查取消标志并优雅退出。
好处是**已完成的成果可以保留**（部分卷），而不是像强杀那样全丢。

---

## 10. 本节小结

| 环节 | 手段 | 一句话原理 |
|---|---|---|
| 切片 | 两级切分 + 200 字重叠 | 切分点不能是信息断裂点 |
| 编码 | embedding | 把语义变成可算距离的向量 |
| 比较 | 余弦相似度 | 只看方向不看长度 |
| 加速 | numpy 矩阵化 | 一次 BLAS 胜过千次循环 |
| 融合 | RRF | 丢分数保排名，两路都上榜者胜 |
| 领域补丁 | heading_bonus | 精确信号覆盖模糊语义 |
| 兜底 | 三级降级 + 均匀采样 | 宁可降质，不可失败 |
| 约束 | 布鲁姆层级 + 禁用资料外知识 | 用约束替代请求 |
| 质检 | 抽检升级 | 验收抽样：好批次快速放行 |
| 抗损耗 | 带余量生成 + 按需截断 | 安全库存 |
| 防幻觉 | source_id 白名单校验 | 受限生成 |
| 并发 | rollback 换快照 | 轮询要防陈旧快照 |

---

## 11. 检索练习

**Q1.** `vector_rank` 里 `idx = np.array([i for i, _ in usable])` 这一行，删掉会怎样？

**Q2.** RRF 用排名而不是分数，核心原因是什么？`_RRF_K` 调成 1 会有什么变化？

**Q3.** 为什么「生成时看 3 万字、自检时看 800 字」会导致大卷 0 产出？

**Q4.** 近似判重阈值 0.6 是根据什么定的？为什么 fake 模式要跳过判重？

**Q5.** `wait_embed_ready` 里的 `db.rollback()` 删掉会怎样？

<details>
<summary>参考答案</summary>

**A1.** 返回的索引会变成「子集下标」而不是「原 chunks 下标」。分数是对的，但引用关系全错 —— 溯源指向错误切片。因为 `mat` 只包含 `has_vec=True` 的切片，与原数组下标不对应。

**A2.** 向量相似度（0~1 小数）与关键词命中数（整数）量纲不同，不能相加；排名是序数，天然可比。`_RRF_K` 调成 1 会**放大头部**：第 1 名 0.5、第 2 名 0.33、第 3 名 0.25 —— 单通道的第一名权重过大，「两路都上榜」的优势被削弱，融合退化成「谁在向量里靠前谁赢」。

**A3.** 依据后段切片出的题，自检时看不到那段原文 → 必然判「无依据」→ 判不合格 → 全部被否 → 产出 0。根因是**审核方与生成方的证据预算不一致**。

**A4.** 实测：真实语义重复的 Jaccard 约 0.625，不同考点通常 < 0.4，取 0.6 能拦重复且不误杀。fake 模式跳过是因为它生成的是同模板伪题，彼此相似度约 0.8 —— 不跳过会把 12 道误杀到只剩 3 道。

**A5.** 事务在 MVCC 下一直读**事务开始时的快照**，看不到 embed 线程后续提交的 `embed_status=ok` → `_pending_count()` 永远 > 0 → 每次都等到 20 秒超时返回 False → 检索静默退化成关键词模式。

</details>

---

## 12. 延伸阅读

- **RAG 原始论文**：Lewis et al., *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks*, NeurIPS 2020（arXiv:2005.11401）
- **RRF 原始论文**：Cormack, Clarke & Buettcher, *Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods*, SIGIR 2009
- **numpy 广播**：[NumPy 官方 "Broadcasting" 教程](https://numpy.org/doc/stable/user/basics.broadcasting.html)
- **pgvector**：官方 README 的 `<=>` / `<->` / `<#>` 三个算子说明
- **布鲁姆分类学**：搜索 `Bloom's Taxonomy revised`（Anderson & Krathwohl 版本更贴合命题）
- **验收抽样**：质量管理的 AQL（Acceptable Quality Limit）概念

---

**上一节** → [第 01 节 · 官方题库闯关](./01-官方题库闯关.md)
**下一节** → [第 03 节 · AI 模拟答与事件溯源](./03-AI模拟答与事件溯源.md)
**术语不懂** → [术语表](./reference/术语表.md)
