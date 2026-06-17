# RAG 与 LangChain / LangGraph 的联系手册

> 「RAG + LangChain + LangGraph」是网上视频的固定三件套标题。这一份讲清楚：这三者到底什么关系、为什么总被凑在一起讲、以及你该怎么理解它们的分工。
>
> 编写时间：2026 年 6 月

---

## 定位目录

**按需求快速定位：**

| 你想知道 | 直接看 |
|---|---|
| 视频为什么总把三件套放一起讲 | [第 0 章](#第-0-章-先回答为什么是这三件套) |
| RAG 与 LangChain 什么关系 | [第 1 章](#第-1-章-rag-与-langchain-的联系) |
| **RAG 与 LangGraph 什么关系（含代码分界线）** | [第 2 章](#第-2-章-rag-与-langgraph-的联系) |
| 三者对照总表 | [第 3 章](#第-3-章-三者关系总表) |
| 我的项目该用哪套技术栈 | [第 4 章](#第-4-章-三种典型的技术栈组合) |
| 面试怎么讲这三者 | [第 5 章](#第-5-章-面试怎么讲这三者) |
| 一页速查 | [第 6 章](#第-6-章-一页速查) |
| 零散疑问快答 | [附录](#附录常见疑问快答) |

**完整章节：**

- [第 0 章 先回答「为什么是这三件套」](#第-0-章-先回答为什么是这三件套)
  - [0.1 它们分别处在三个不同的层次上](#01-它们分别处在三个不同的层次上)
  - [0.2 为什么视频非要把它们凑一起](#02-为什么视频非要把它们凑一起)
  - [0.3 所以正确的理解姿势](#03-所以正确的理解姿势)
- [第 1 章 RAG 与 LangChain 的联系](#第-1-章-rag-与-langchain-的联系)
  - [1.1 一句话：LangChain 是 RAG 的「标准零件箱」](#11-一句话langchain-是-rag-的标准零件箱)
  - [1.2 但真正值钱的只有一个抽象：Retriever 接口](#12-但真正值钱的只有一个抽象retriever-接口)
  - [1.3 一个必须知道的历史事实](#13-一个必须知道的历史事实)
- [第 2 章 RAG 与 LangGraph 的联系](#第-2-章-rag-与-langgraph-的联系)
  - [2.1 一句话：LangGraph 是 RAG 的「流程编排器」](#21-一句话langgraph-是-rag-的流程编排器)
  - [2.2 为什么链式表达不了循环](#22-为什么链式表达不了循环)
  - [2.3 落成代码看这条分界线](#23-落成代码看这条分界线)
  - [2.4 LangGraph 给 RAG 带来的，不只是「能循环」](#24-langgraph-给-rag-带来的不只是能循环)
  - [2.5 一个重要的反向提醒](#25-一个重要的反向提醒)
- [第 3 章 三者关系总表](#第-3-章-三者关系总表)
  - [3.1 一张图收尾](#31-一张图收尾)
- [第 4 章 三种典型的技术栈组合](#第-4-章-三种典型的技术栈组合)
  - [组合一：LangChain 单打（大多数情况）](#组合一langchain-单打大多数情况)
  - [组合二：LangChain 零件 + LangGraph 编排（进阶，也最实用）](#组合二langchain-零件--langgraph-编排进阶也最实用)
  - [组合三：多 Agent 协作（复杂场景）](#组合三多-agent-协作复杂场景)
  - [4.1 组合选择流程图](#41-组合选择流程图)
- [第 5 章 面试怎么讲这三者](#第-5-章-面试怎么讲这三者)
  - [5.1 高频问题的标准答法](#51-高频问题的标准答法)
  - [5.2 减分项](#52-减分项)
- [第 6 章 一页速查](#第-6-章-一页速查)
  - [6.1 三句话记住全部](#61-三句话记住全部)
  - [6.2 六种 RAG 流派 × 需要哪层](#62-六种-rag-流派--需要哪层)
  - [6.3 配套手册怎么配合用](#63-配套手册怎么配合用)
- [附录：常见疑问快答](#附录常见疑问快答)
- [参考来源](#参考来源)

---

## 第 0 章 先回答「为什么是这三件套」

你连着两次问到的都是同一个现象——**网上把 RAG、LangChain、LangGraph 放在一起讲**。这不是巧合，是一个有明确成因的组合。**理解了这个成因，你对这三者的关系就通了。**

### 0.1 它们分别处在三个不同的层次上

这是最核心的一点。**三个词不是同类的，它们在回答三个不同的问题：**

| 层次 | 回答的问题 | 对应 | 类比（做菜） |
|---|---|---|---|
| **目标层** | 我要做什么？ | **RAG** | 「今天做红烧肉」 |
| **模式层** | 我的流程是什么形状？ | 直线 / 循环 | 「先炖后收汁」——工序 |
| **工具层** | 我用什么把它实现出来？ | **LangChain / LangGraph** | 「用高压锅还是砂锅」 |

**RAG 是「要做什么」，LangChain/LangGraph 是「怎么实现它」。它们压根不在同一个维度上，所以「RAG vs LangChain」这种问法本身就是错位的。**

### 0.2 为什么视频非要把它们凑一起

四个原因，从表面到本质：

**原因一：RAG 是 LangChain 最早、最经典的杀手级用例。**

2023 年 LangChain 火起来，靠的就是「文档问答」这个 demo——加载 PDF、切块、存向量库、检索、生成，十来行代码就能跑通。**RAG 和 LangChain 在传播史上是绑定的。** 之后 LangGraph 出现，视频作者自然把「新工具」加到「老配方」里，形成三件套。

**原因二：它们串起来是一条完整的学习路径。**

```
RAG（知道要做什么）
  ↓ 用什么工具？
LangChain（把检索和生成串起来）
  ↓ 发现流程需要循环和分支？
LangGraph（把流程编排起来）
```

**这是一条真实的、合理的递进路径**，所以视频这么排是有道理的。**问题在于大部分视频不讲「什么时候该停在哪一步」。**

**原因三：三件套 = 一个完整的项目标题。**

「RAG + LangChain + LangGraph 打造企业级知识库」——这个标题里，RAG 给方向、LangChain 给工具、LangGraph 给「高级感」。**三个词凑齐了就显得项目很完整。** 而且这确实是最常见的真实项目形态。

**原因四（必须说的）：有相当多的内容是「为用而用」。**

**这个要提醒你，因为它是面试里最容易暴露的东西。**

真实情况是：**很多 RAG 项目根本不需要 LangGraph。** 一个文档问答系统，Advanced RAG 的三件套（Query 改写 + 混合检索 + Rerank）用 LangChain 的链式组合就完全够用。但视频里非要用 LangGraph 画个图，因为「图」看起来更专业。

> **面试官最讨厌的就是这个。** 如果你说「我用 LangGraph 搭了一个 RAG 系统」，他大概率会追问：**「你这个流程里，哪一步需要循环？哪一步需要人工中断？为什么不用链式实现？」**
>
> **答不上来，你前面讲的所有技术栈都会被打折。**

### 0.3 所以正确的理解姿势

```
┌─────────────────────────────────────────────────────────┐
│  第 1 层：RAG 是一种架构模式                              │
│  「检索外部知识，喂给模型，让它答得更准」                   │
│  它是 what，与技术选型无关                                │
└───────────────────────┬─────────────────────────────────┘
                        │ 落地时会变成什么形状？
                        │
        ┌───────────────┴───────────────┐
        │                               │
   ┌────▼─────┐                   ┌─────▼──────┐
   │  直线     │                   │  循环/分支  │
   │ Naive    │                   │ Self-RAG   │
   │ Advanced │                   │ CRAG       │
   │ Modular  │                   │ Agentic RAG│
   └────┬─────┘                   └─────┬──────┘
        │                               │
   ┌────▼─────────────┐        ┌────────▼─────────────┐
   │  LangChain 就够   │        │  需要 LangGraph       │
   │  （链式组合）      │        │  （状态图 + 循环）     │
   └──────────────────┘        └──────────────────────┘
```

**这张图就是全部答案。** 后面几章把它展开成细节。

---

## 第 1 章 RAG 与 LangChain 的联系

### 1.1 一句话：LangChain 是 RAG 的「标准零件箱」

RAG 的每个环节，LangChain 几乎都有对应的抽象：

| RAG 环节（你手册里的七步流程） | LangChain 提供的抽象 |
|---|---|
| 加载文档 | **Document Loaders**（PDF / Word / HTML / Notion / DB 等上百种） |
| 清洗解析 | **Document Transformers** |
| 分块 | **Text Splitters**（固定长度 / 结构感知 / 语义） |
| 向量化 | **Embeddings**（OpenAI / Voyage / BGE / 本地模型） |
| 存入向量库 | **VectorStore 集成**（Pinecone / Qdrant / Milvus / pgvector） |
| 检索 | **Retriever 接口**（向量 / BM25 / 混合 / 多查询 / 父子文档） |
| 拼 Prompt | **Prompt Templates** |
| 生成 | **Chat Models**（provider 无关） |
| 评估 | 配合 LangSmith / Langfuse 的 Evaluator |

**这就是为什么 RAG 教程里全是 LangChain**——它是这个领域零件最全的箱子。

### 1.2 但真正值钱的只有一个抽象：Retriever 接口

零件多不算本事，**LangChain 对 RAG 最实质的贡献是把「检索」统一成了一个接口。**

```python
docs = retriever.invoke("怎么配置超时？")
```

**就这么简单一行，但它意味着：**

```python
# 换个检索策略，下游代码完全不用改
retriever = vectorstore.as_retriever(search_kwargs={"k": 5})
retriever = BM25Retriever.from_documents(docs)
retriever = MultiQueryRetriever.from_llm(retriever=base, llm=llm)
retriever = ParentDocumentRetriever(vectorstore=vs, docstore=store)
retriever = EnsembleRetriever(retrievers=[bm25, vector], weights=[0.4, 0.6])
```

**五种完全不同的检索策略，调用方式完全一致。** 这才是 LangChain 在 RAG 里的真正价值——**它让「混合检索」「多路召回」「策略对比实验」的实现成本降到很低。**

> **面试时可以这么讲**：「我用 LangChain 的 Retriever 抽象来统一多路召回，因为它的接口设计让混合检索的组合成本很低——我可以把 BM25 和向量检索用 `EnsembleRetriever` 组合起来做对比实验。**但 BM25 的 k1、b 参数和 RRF 的融合策略是我自己调的**，框架只提供组合能力，效果还是靠这些参数。」
>
> **这段话的结构值得学**：先说框架解决了什么工程问题，再强调效果取决于你对原理的理解。**既证明了你会用工具，又证明你不是只会调包。**

### 1.3 一个必须知道的历史事实

你 RAG 手册里提到过一句关键判断：

> 「如果你被问『你会怎么搭建一个 RAG 系统』，而你的回答从『我用 LangChain 包一下』开始，就已经被判为只会基础了。」

**为什么这句话成立？** 因为 LangChain 在 RAG 里的角色是**降低实现成本**，不是**提升效果**。

一个真实的对照：

| | 用 LangChain 默认配置 | 做了优化 |
|---|---|---|
| 切块 | `RecursiveCharacterTextSplitter(chunk_size=1000)` | 结构感知切块 + 父子文档，chunk 150-300 |
| 检索 | 纯向量 top-4 | 元数据预过滤 + 混合检索 + RRF |
| 精排 | 无 | Cross-Encoder 重排 top 3-5 |
| 效果 | 能用 | 明显更好 |

**左边是 LangChain 一行代码就能给的，右边是框架给不了的。** 面试官想听的是右边。

> **所以正确的定位是**：LangChain 把你从「写连接器」的苦力活里解放出来，让你有时间去做真正影响效果的调优。**它是起点，不是卖点。**

---

## 第 2 章 RAG 与 LangGraph 的联系

这一章是重点，因为**这里有一条清晰的技术分界线**。

### 2.1 一句话：LangGraph 是 RAG 的「流程编排器」

**LangGraph 只在你需要「循环」或「分支」时才有用。**

先看你的 RAG 手册里那条主线——六种流派的流程形态：

| 流派 | 流程形态 | 有循环吗 | 有分支吗 | 需要 LangGraph |
|---|---|---|---|---|
| **Naive RAG** | 加载→切块→存→查→拼→生成 | ❌ | ❌ | **不需要** |
| **Advanced RAG** | Query改写→混合检索→重排→生成 | ❌ | ❌ | **不需要** |
| **Modular RAG** | Router 分发到不同链路 | ❌ | ✅（简单） | **看情况** |
| **Graph RAG** | 实体抽取→建图→社区摘要→查询路由 | ❌ | ✅（明显） | **建议** |
| **Self-RAG** | 检索→生成→自我打分→**不满意就重来** | ✅ | ✅ | **必须** |
| **Agentic RAG** | 规划→多轮检索→验证→综合 | ✅ | ✅ | **必须** |

**看最后两行。** 「不满意就重来」和「多轮检索」——这就是循环。**而 LangChain 的链是有向无环的，它表达不了循环，这就是 LangGraph 存在的意义。**

### 2.2 为什么链式表达不了循环

这不是技术限制，是**模型层面**的必然：

| | LangChain 的 Chain | LangGraph 的 Graph |
|---|---|---|
| **数据流** | **管道**：上一个的输出 → 下一个的输入 | **黑板**：所有节点读写同一块共享状态 |
| 状态 | 不保留（流过去就没了） | 每个 superstep 持久化 |
| 能循环吗 | ❌ 循环会导致数据流无法定义 | ✅ 状态持续存在，可以回头 |
| 能暂停吗 | ❌ | ✅ interrupt |

**关键在「管道 vs 黑板」这个区别。**

- **管道模型**：A 的输出给 B，B 的输出给 C。如果 C 要回到 A，那 A 的输入是谁给的？**数据流定义不下去了。**
- **黑板模型**：所有节点往同一块状态上读写。A 写进去，C 读出来发现不合格，改了状态让 B 重跑。**没问题，因为状态一直在那。**

**所以 LangGraph 不是「LangChain 的升级版」，它解决的是一个 LangChain 结构上就解决不了的问题：让流程能回头看。**

### 2.3 落成代码看这条分界线

**Advanced RAG（直线，LangChain 就够）：**

```python
from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain.retrievers import EnsembleRetriever, ContextualCompressionRetriever

# 混合检索 + 重排
retriever = ContextualCompressionRetriever(
    base_compressor=reranker,
    base_retriever=EnsembleRetriever(retrievers=[bm25, vector], weights=[0.4, 0.6]),
)

chain = (
    {"context": retriever | format_docs, "question": RunnablePassthrough()}
    | ChatPromptTemplate.from_template(
        "根据以下资料回答问题，并标注引用来源。\n\n资料：\n{context}\n\n问题：{question}"
    )
    | init_chat_model("anthropic:claude-sonnet-4-6")
)

chain.invoke("怎么配置超时？")
```

**这就是「生产标配」的 Advanced RAG。它是直线，所以优雅，所以不需要 LangGraph。**

**Self-RAG（有循环，必须 LangGraph）：**

```python
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver

class RAGState(TypedDict):
    question: str
    current_query: str
    docs: list
    answer: str
    retry_count: int

def retrieve(state: RAGState) -> dict:
    return {"docs": retriever.invoke(state["current_query"])}

def grade_documents(state: RAGState) -> dict:
    """判断检索到的文档是否真的相关 —— 一个 LLM 调用（Self-RAG 的 IsREL）。"""
    verdict = judge_llm.invoke(
        f"问题：{state['question']}\n文档：{state['docs']}\n这些文档能回答问题吗？只回答 relevant 或 irrelevant"
    ).content.strip()
    return {"relevance": verdict}

def rewrite_query(state: RAGState) -> dict:
    """不相关就改写查询再试（这就是循环的来源）。"""
    new_q = rewrite_llm.invoke(
        f"原问题：{state['question']}\n上次检索：{state['current_query']}\n"
        f"检索效果不好，请改写一个更容易命中的查询："
    ).content.strip()
    return {"current_query": new_q, "retry_count": state["retry_count"] + 1}

def generate(state: RAGState) -> dict:
    return {"answer": gen_llm.invoke(build_prompt(state)).content}

def route_after_grading(state: RAGState) -> str:
    if state["relevance"] == "relevant":
        return "generate"
    if state["retry_count"] >= 2:        # ← 必须有退出条件，否则递归超限
        return "generate"                 # 放弃重试，用现有材料尽力回答
    return "rewrite"

g = StateGraph(RAGState)
g.add_node("retrieve", retrieve)
g.add_node("rewrite", rewrite_query)
g.add_node("generate", generate)

g.add_edge(START, "retrieve")
g.add_conditional_edges(
    "retrieve", route_after_grading,
    {"generate": "generate", "rewrite": "rewrite"},
)
g.add_edge("rewrite", "retrieve")     # ★★★ 就是这一条回边 ★★★
g.add_edge("generate", END)

app = g.compile(checkpointer=PostgresSaver.from_conn_string(DB_URL))
```

**把两段代码对照着看，你就彻底理解这条分界线了。**

**差别只在一条 `add_edge("rewrite", "retrieve")` 的回边**——「检索不好就改写重来」。

但这条回边带来了三个新问题，**而这三个问题恰好就是 LangGraph 存在的理由**：

| 回边带来的问题 | LangGraph 的答案 |
|---|---|
| 会无限循环吗？ | `retry_count` 计数 + 条件边退出 / `recursion_limit` |
| 循环过程中状态存哪？ | 共享 State（黑板模型） |
| 中断了怎么办？ | Checkpointer 持久化 |

> **面试时可以直接这么讲**：「朴素 RAG 是直线，用 LCEL 链就够。但我做的是带自我纠错的 RAG——检索后先评估文档相关性，不相关就改写查询重试。这个**循环**用 LangGraph 的状态图表达最自然。同时我加了重试次数上限和退出条件，因为**有环就一定会有递归超限风险**，不能靠默认的 25 兜底。」
>
> **这段话的杀伤力在于最后那句**——它证明的不仅是「我会用 LangGraph」，而是「我知道它为什么会坏」。

### 2.4 LangGraph 给 RAG 带来的，不只是「能循环」

很多人以为 LangGraph 对 RAG 的贡献就是「能循环」——**其实循环只是入门理由，真正有价值的是下面这些：**

| LangGraph 能力 | 对 RAG 系统的实际意义 |
|---|---|
| **Checkpointer** | 多轮追问时不用重复检索；会话状态跨请求保持；「刚才那个问题换个说法」能理解 |
| **条件边** | **查询路由**：技术问题走向量库、数据问题走 SQL、时效问题走 Web、闲聊直接答不检索 |
| **interrupt** | 检索到低置信度内容或涉及敏感信息时，人工确认后再生成（金融、医疗刚需） |
| **子图** | 每个数据源一个独立检索子 Agent，各自有独立的检索策略和参数 |
| **并行分支** | 同时查向量库 + BM25 + 知识图谱，Fan-In 汇总 —— 比你写 `asyncio.gather` 更清晰且可持久化 |
| **Streaming** | 显示「正在检索…找到 5 篇文档…正在评估相关性…正在生成」的进度反馈 |
| **Time travel** | **用户投诉「你上次答错了」，回放那次运行，看到当时究竟检索到了哪几篇文档** |

**最后那一行是我认为最被低估的能力。**

RAG 系统最痛的调试场景就是「这个答案为什么是错的」：

- **纯链式实现**：你只能重跑一次，然后祈祷问题复现。因为检索结果、检索到的文档、当时用的 query 全都随运行消失了。
- **有 checkpoint**：你能精确回到那次运行，看到**当时检索到的究竟是哪几篇文档、评了什么样的相关性分数、为什么走到了生成分支**。

> **这是可以直接写进简历的一句话**：「用 LangGraph 的 checkpoint 持久化实现运行回放，线上问题定位从『重跑碰运气』变成『回放看现场』。」

### 2.5 一个重要的反向提醒

**别为了用 LangGraph 而用 LangGraph。** 你 RAG 手册里那句话是对的：

> 「⚠️ 重要认知：**纯 Agentic RAG 目前更适合离线分析场景**，在线实时场景仍以 Advanced RAG 为主。不要一上来就做 Agentic，会被延迟和成本拖死——这是面试官最喜欢追问的『过度设计』陷阱。」

补上 LangGraph 视角的版本：

**判断标准很简单——你能明确说出「哪一条边是回边」吗？**

- 「因为我的检索如果质量不够要重试」→ ✅ 有回边，该用
- 「因为我的查询可能走三条不同链路」→ ✅ 有分支，该用（但先考虑用 Router 链能否满足）
- 「因为 LangGraph 比较火」→ ❌ 别用

**答不出「哪条边是回边」，就说明你的流程是直线，用链式实现更简单、更易维护、更少故障点。**

---

## 第 3 章 三者关系总表

把所有内容压缩成一张可以背下来的表：

| | RAG | LangChain | LangGraph |
|---|---|---|---|
| **是什么** | 架构模式 | Agent 构建框架 | 编排运行时 |
| **层次** | 目标层（what） | 工具层（how） | 工具层（how，更低阶） |
| **核心问题** | 检索外部知识喂给模型 | 怎么快速把零件串起来 | 怎么让流程能循环/暂停/恢复 |
| **在 RAG 里负责** | — | 加载、切块、向量化、检索、拼 Prompt、生成 | 多步编排、循环重试、路由分发、状态持久化 |
| **直线 RAG 需要吗** | 是（就是它本身） | ✅ 需要 | ❌ 不需要 |
| **循环 RAG 需要吗** | 是 | ✅ 需要（零件） | ✅ 需要（编排） |
| **能否单独用** | — | 能（直线场景足够） | 能，但零件还是用 LangChain 的省事 |
| **官方定位原话** | — | "the agent framework" | "the orchestration runtime" |

### 3.1 一张图收尾

```
                     ┌──────────────────────┐
                     │   RAG（要做什么）      │
                     │   检索增强生成         │
                     └───────────┬──────────┘
                                 │
                 ┌───────────────┴───────────────┐
                 │                               │
        ┌────────▼────────┐             ┌────────▼─────────┐
        │   直线型 RAG     │             │   循环型 RAG      │
        │                │             │                  │
        │ Naive RAG      │             │ Self-RAG         │
        │ Advanced RAG   │             │ CRAG             │
        │ Modular RAG    │             │ Agentic RAG      │
        └────────┬────────┘             └────────┬─────────┘
                 │                               │
                 │ 用链式组合                     │ 用状态图编排
                 │                               │
        ┌────────▼────────────────────────────────▼─────────┐
        │                                                   │
        │   LangChain  ← 提供零件：Loaders / Splitters /     │
        │                 Retriever / Tools / Models         │
        │                                                   │
        │   LangGraph  ← 提供编排：StateGraph / 条件边 /      │
        │                 Checkpointer / interrupt           │
        │                                                   │
        │   （LangChain 的 create_agent 底层跑在 LangGraph 上）│
        └───────────────────────────────────────────────────┘
```

---

## 第 4 章 三种典型的技术栈组合

网上视频讲的三件套，实际落地会有三种形态。**认清自己属于哪一种，比盲目堆技术栈重要得多。**

### 组合一：LangChain 单打（大多数情况）

```
RAG（直线）→ LangChain 链式组合
```

**适合**：文档问答、FAQ 机器人、企业知识库。

**技术栈**：`Loaders + Splitters + Embeddings + Retriever + LLM`

**特征**：代码量少、故障点少、延迟低、好维护。

**不要因为它是「基础版」就觉得拿不出手**——你 RAG 手册里写着，**Advanced RAG 是当前绝大多数生产系统的真实形态**。交付一个跑得稳的 Advanced RAG，比交付一个跑不动的 Agentic RAG 强得多。

### 组合二：LangChain 零件 + LangGraph 编排（进阶，也最实用）

```
RAG（循环/分支）→ LangChain 造零件 + LangGraph 编排
```

**适合**：带自我纠错的检索、多数据源路由、需要多轮追问的知识库。

**技术栈**：

```
LangGraph StateGraph           ← 画流程
├── Node: retrieve             ← 内部用 LangChain 的 Retriever
├── Node: grade_documents      ← 一个 LLM 调用
├── Node: rewrite_query        ← 一个 LLM 调用
├── Node: generate             ← 内部用 LangChain 的 Prompt + Model
└── Checkpointer: Postgres     ← 持久化
```

**特征**：图是自己画的，但节点内部的零件全是 LangChain 的。**这是大多数「企业级」项目的真实结构，也是三件套标题背后的真相。**

### 组合三：多 Agent 协作（复杂场景）

```
RAG（多个专业检索 Agent）→ LangGraph 多 Agent 拓扑
```

**适合**：多业务线、多数据源、需要跨领域综合的研究型任务。

**技术栈**：

```python
# 每个数据源一个用 LangChain 快速搭好的 agent
docs_agent  = create_agent(model, tools=[search_docs, read_pdf])
sql_agent   = create_agent(model, tools=[query_sql])
web_agent   = create_agent(model, tools=[web_search])

# 用 LangGraph 编排它们
supervisor_graph = ...   # Supervisor 拓扑
```

**代价要知道**：延迟高、成本高、调试难。**这是 Agentic RAG 的形态，你手册里明确说过它目前更适合离线分析场景。**

### 4.1 组合选择流程图

```
你的 RAG 系统需要什么？
│
├─ 只是「问问题 → 检索 → 回答」
│    → 组合一：LangChain 单打
│    → 先把 Advanced RAG 三件套做好（改写 + 混合检索 + Rerank）
│
├─ 需要「检索质量不好就重试」或「按问题类型走不同链路」
│    → 组合二：LangChain + LangGraph
│    → 你能说出哪条边是回边吗？说不出就回到组合一
│
├─ 需要「多个数据源各配一个专家」
│    → 组合三：多 Agent
│    → 先问：延迟预算够吗？真的需要吗？
│
└─ 需要「人工审核后再生成」
     → 组合二，加 interrupt 节点
```

---

## 第 5 章 面试怎么讲这三者

### 5.1 高频问题的标准答法

**Q1：RAG 和 LangChain 是什么关系？**

> **答**：RAG 是架构模式，LangChain 是实现它的工具箱，两者不同层次。LangChain 在 RAG 里的价值是提供了全链路的抽象——Loaders、Splitters、Embeddings、VectorStore 集成，以及最关键的 **Retriever 统一接口**。这个接口的价值在于让我能用同样的方式调用向量检索、BM25、混合检索、多查询检索，**做策略对比实验的成本很低**。
>
> **但要强调的是**：框架只解决实现成本，不解决效果。真正决定 RAG 效果的是切块策略、混合检索的权重、RRF 的融合参数、Rerank 模型的选择——**这些框架给不了，得靠对原理的理解。**

**Q2：为什么你的 RAG 用了 LangGraph？**

> **这个问题的答法决定成败。** 如果你只是「为了用它」，一定会被拆穿。正确的答法是**先讲清楚那个循环**：
>
> 「朴素 RAG 是直线，用 LCEL 链就够，我一开始也是这么做的。但我发现一个问题是：**如果检索到的文档不相关，链路会硬着头皮往下走，最后生成一个看起来很自信但其实是错的答案。** 这就是 RAG 的『有上下文仍幻觉』失败模式。
>
> 所以我在检索后加了一个相关性评估节点——这是个 LLM 调用。评估不通过就走改写分支重试。**这个『检索→评估→不通过→改写→再检索』就是一个循环，链式表达不了，所以用 LangGraph。**
>
> 另外我加了两个防护：**重试次数上限设为 2**，因为不设的话必然会撞上递归超限；退出时如果还没找到相关文档，就走一个明确的降级分支——**返回「我找不到相关依据」而不是编一个答案**。」

**这个答法的三个得分点**：① 从失败模式出发而非从工具出发；② 明确指出循环在哪；③ 主动提防护措施和降级策略。

**Q3：什么时候 RAG 不需要 LangGraph？**

> **答**：**只要流程是直线就不需要。** Naive RAG 和 Advanced RAG 都是直线——Query 改写、混合检索、Rerank 这些优化都是「检索前-检索中-检索后」的串联，没有回边，用 LCEL 链式组合就够，而且更简单、故障点更少、延迟更低。
>
> **我判断的标准是：能不能说出一条明确的回边。** 说不出回边就说明是直线。**强行上 LangGraph 会引入状态管理、checkpointer 运维、递归超限这些新问题，而收益为零。**

**这个回答会让面试官觉得你有工程判断力，而不是技术堆砌者。**

**Q4：LangGraph 除了循环，还给 RAG 带来什么？**

> **答**：四个实际价值。① **查询路由**——按问题类型分发到向量库/SQL/Web/不检索，这是条件边；② **checkpoint 持久化**——多轮追问不用重复检索，还能**回放线上出错的运行看到当时检索到了什么**；③ **interrupt**——检索到低置信度内容时人工确认再生成；④ **并行分支**——同时查多个检索源再 Fan-In 汇总，比 `asyncio.gather` 更清晰且状态可持久化。
>
> **其中我认为最实用的是运行回放**——RAG 最痛的调试场景是「这个答案为什么错」，纯链式只能重跑碰运气，有 checkpoint 就能精确回到现场。

### 5.2 减分项

| 减分说法 | 问题 | 该怎么改 |
|---|---|---|
| 「我用 LangChain 包了一下 RAG」 | 从工具开始讲，暴露只会调包 | 从检索策略和取舍开始讲 |
| 「我用 LangGraph 搭了 RAG」 | 大概率说不出回边 | 主动说明哪一步需要循环 |
| 「三件套全用上了」 | 把堆栈当卖点 | 说明每层的必要性，包括为什么不用某层 |
| 「RAG 就是 LangChain 的文档问答」 | 混淆架构模式与实现 | 分清 what 和 how |
| 「LangGraph 比 LangChain 高级所以更好」 | 层次错位 | 它们是不同层，不是高低关系 |
| 「我的 RAG 用了 Agentic 架构」 | 大概率延迟和成本失控 | 说明业务是否真的需要 |

---

## 第 6 章 一页速查

### 6.1 三句话记住全部

1. **RAG 是「做什么」，LangChain/LangGraph 是「怎么做」——不同层次，不构成选择题。**
2. **直线 RAG 用 LangChain 就够；有回边的 RAG（Self-RAG / CRAG / Agentic）才需要 LangGraph。**
3. **判断标准：你能说出哪条边是回边吗？说不出就是直线，别上 LangGraph。**

### 6.2 六种 RAG 流派 × 需要哪层

| 流派 | LangChain | LangGraph |
|---|---|---|
| Naive RAG | ✅ 够用 | ❌ |
| Advanced RAG | ✅ 够用 | ❌ |
| Modular RAG | ✅ | ⚠️ 分支复杂时用 |
| Graph RAG | ✅ | ✅ 建议 |
| Self-RAG | ✅ 零件 | ✅ **必须** |
| Agentic RAG | ✅ 零件 | ✅ **必须** |

### 6.3 配套手册怎么配合用

| 手册 | 回答 | 什么时候翻 |
|---|---|---|
| **《RAG 系统学习手册》** | 检索怎么做才有效 | 设计检索策略、准备 RAG 面试 |
| **《LangChain 与 LangGraph 系统手册》** | 工具怎么用、怎么避坑 | 写代码、排查生产问题 |
| **《LangChain 与 LangGraph 关系手册》** | 两者之间怎么连 | 技术选型、面试被问两者关系 |
| **本文** | RAG 与它们怎么连、三件套怎么理解 | 项目定架构、面试被问「为什么用」 |

**一句话的配合逻辑**：**原理看第一份，工具看第二份，层次关系看后两份。面试的核心永远是原理加取舍，工具只是载体。**

---

## 附录：常见疑问快答

**Q：我学 RAG 需要先学 LangChain 吗？**

**不需要，但实践时会自然用到。** 你先读 RAG 手册建立原理认知（切块策略、混合检索、RRF、重排、评估指标），这些全都与框架无关。然后动手做项目时用 LangChain 快速搭起来——**顺序是原理先行，工具跟上。**

反过来做有个明显风险：你会把 LangChain 的默认行为当成 RAG 的「标准做法」。比如 `chunk_size=1000` 是 LangChain 的默认值，**但它不是最优值**（你手册里提到 2026 年实践已降到 150-300）。**先学原理，你才有判断默认值好坏的能力。**

**Q：只会 RAG + LangChain，不会 LangGraph，能找到工作吗？**

**能。** 大量「AI 应用开发」岗位的日常就是做知识库问答、文档助手这类直线型 RAG。**把 Advanced RAG 做到位（尤其是评估体系），比会写复杂的图更有竞争力。**

但如果你要投的 JD 里明确写了「Agent」「工作流编排」「多智能体」，那就得补 LangGraph——并且要能讲清「什么时候该用」。

**Q：视频里那种「RAG + LangChain + LangGraph 企业级项目」，值得跟着做吗？**

**值得做，但要带着批判眼光做。** 做完之后问自己三个问题：

1. **这个项目里的图，回边在哪？** 如果找不到，说明作者是为了用而用
2. **如果去掉 LangGraph 会怎样？** 如果只是代码变短但功能不变，说明可以去掉
3. **它做了效果评估吗？** 如果只有「效果不错」没有指标对比，那是个 Demo 不是项目

**能回答这三个问题，你就比视频作者更懂这个项目了——而这正是面试时的差距所在。**

**Q：为什么感觉 RAG 的教程都在讲 LangChain，但面试官又看不起「只会 LangChain」？**

**因为这两件事说的是不同的东西。** 教程讲 LangChain 是因为它降低了「让你先跑起来」的门槛；面试官看不起的是「把门槛降低当成能力」。

打个比方：会用 Excel 不等于会做财务分析。**LangChain 就是 RAG 领域的 Excel——它让所有人能上手，但专业价值在于你用它的默认功能之外，还做了什么判断和优化。**

---

## 参考来源

- LangChain 官方文档：Document Loaders、Text Splitters、Retriever 接口、VectorStore 集成章节
- LangGraph 官方文档：StateGraph、Checkpointer、Conditional Edges、Subgraph 章节
- LangChain 官方博客《LangChain and LangGraph Agent Frameworks Reach v1.0 Milestones》（2025-10）
- Self-RAG 论文（Asai et al.）关于反思令牌与检索评估的机制；CRAG 论文关于检索评估器三路分支的设计
- 与本仓库三份手册交叉引用：《RAG 系统学习手册》第 1 章（六种流派分类与选型）、第 6 章（高级架构逐一拆解）、第 9 章（完整参考架构）；《LangChain 与 LangGraph 系统手册》第 1 章（生态分层）、第 3 章（StateGraph 三原语）、第 6 章（选型决策）；《LangChain 与 LangGraph 关系手册》第 2 章（与 RAG 的联系）

**时效性说明**：RAG 的架构模式（直线 vs 循环）与框架的层次关系（LangChain 在 LangGraph 之上）是两个相对稳定的判断，不随版本变化。但具体的 API 写法会变——LangChain 1.0 于 2025-10 发布，已弃用 `create_react_agent` 和 `AgentExecutor`。**本文的代码示例以 LangChain 1.x / LangGraph 1.x 为准。**
