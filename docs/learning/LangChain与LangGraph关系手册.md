# LangChain 与 LangGraph 的关系手册

> 承接《LangChain 与 LangGraph 系统手册》。这一份专门讲**联系**——它们彼此之间的联系、它们与 RAG 的联系、以及为什么视频总把它们放一起讲。
>
> 编写时间：2026 年 9 月

---

## 定位目录

**按需求快速定位：**

| 你想知道 | 直接看 |
|---|---|
| 视频为什么总把两者放一起讲 | [第 0 章](#第-0-章-先回答为什么视频总把它们放一起讲) |
| 两者到底是什么关系（含代码对照） | [第 1 章](#第-1-章-联系的本质一层封装不是两个工具) |
| **它们和 RAG 什么关系** | [第 2 章](#第-2-章-与-rag-的联系这一章对你最关键) |
| 一张图看懂三者层次 | [第 3 章](#第-3-章-三者关系总图) |
| Chain / Retriever / Agent 等名词混淆 | [第 4 章](#第-4-章-五组最容易混淆的组合辨析) |
| 我该按什么顺序学 | [第 5 章](#第-5-章-学习顺序建议针对你的情况) |
| 一页速查 / 判断流程图 | [第 6 章](#第-6-章-一页速查) |
| 零散疑问快答 | [附录](#附录常见疑问快答) |

**完整章节：**

- [第 0 章 先回答「为什么视频总把它们放一起讲」](#第-0-章-先回答为什么视频总把它们放一起讲)
  - [原因一：它们就是同一个东西的上下两层](#原因一它们就是同一个东西的上下两层)
  - [原因二：视频的流量逻辑](#原因二视频的流量逻辑)
  - [原因三（真正的答案）：因为做 AI 应用它们必须一起用](#原因三真正的答案因为做-ai-应用它们必须一起用)
- [第 1 章 联系的本质：一层封装，不是两个工具](#第-1-章-联系的本质一层封装不是两个工具)
  - [1.1 用一句话说清](#11-用一句话说清)
  - [1.2 从代码上看这个联系](#12-从代码上看这个联系)
  - [1.3 联系的三种具体形态](#13-联系的三种具体形态)
  - [1.4 用「能力对照」再确认一次](#14-用能力对照再确认一次)
- [第 2 章 与 RAG 的联系（这一章对你最关键）](#第-2-章-与-rag-的联系这一章对你最关键)
  - [2.1 先给结论](#21-先给结论)
  - [2.2 用需求对映三层](#22-用需求对映三层)
  - [2.3 这就是 RAG 六种流派与两层的对应关系](#23-这就是-rag-六种流派与两层的对应关系)
  - [2.4 落成代码看这个联系](#24-落成代码看这个联系)
  - [2.5 反过来：LangGraph 让 RAG 从「能答」到「可靠」](#25-反过来langgraph-让-rag-从能答到可靠)
- [第 3 章 三者关系总图](#第-3-章-三者关系总图)
- [第 4 章 五组最容易混淆的组合辨析](#第-4-章-五组最容易混淆的组合辨析)
  - [4.1 LangChain 的 Chain vs LangGraph 的 Graph](#41-langchain-的-chain-vs-langgraph-的-graph)
  - [4.2 Retriever vs VectorStore vs RAG](#42-retriever-vs-vectorstore-vs-rag)
  - [4.3 Agent vs RAG](#43-agent-vs-rag)
  - [4.4 LangGraph vs 普通 Python 状态机](#44-langgraph-vs-普通-python-状态机)
  - [4.5 Middleware vs Node](#45-middleware-vs-node)
- [第 5 章 学习顺序建议（针对你的情况）](#第-5-章-学习顺序建议针对你的情况)
- [第 6 章 一页速查](#第-6-章-一页速查)
  - [6.1 关系一句话](#61-关系一句话)
  - [6.2 判断流程图（贴墙版）](#62-判断流程图贴墙版)
  - [6.3 配套手册定位](#63-配套手册定位)
- [附录：常见疑问快答](#附录常见疑问快答)
- [参考来源](#参考来源)

---

## 第 0 章 先回答「为什么视频总把它们放一起讲」

你观察到的现象是真的，而且有三个层次的原因。**但前两个层次的答案都是「因为省事」，只有第三个才值得你花时间。**

### 原因一：它们就是同一个东西的上下两层

这是最直接的原因。**因为它们确实该放在一起讲——它们本来就是一套栈。**

```
        ┌──────────────────────────┐
        │   LangChain              │  ← 你在这一层写代码
        │   create_agent           │
        │   Middleware             │
        │   Standard content blocks│
        └────────────┬─────────────┘
                     │  create_agent 内部调用
                     ↓
        ┌──────────────────────────┐
        │   LangGraph              │  ← 这一层在替你干活
        │   StateGraph 运行时       │
        │   Checkpointer           │
        │   interrupt / streaming  │
        └──────────────────────────┘
```

一个讲「怎么用方向盘」，一个讲「底盘怎么工作」——**拆开讲才是奇怪的。** 老式教程把它们对立起来，是因为 2024 年以前它们确实是两个独立演进的项目；1.0 之后官方主动把 LangChain 重构到了 LangGraph 运行时上，这个「对立」就消失了。

### 原因二：视频的流量逻辑

不太客气地说，**「LangChain vs LangGraph：到底该学哪个」这个标题本身就比「LangChain 的两层架构」更有点击率。** 「vs」制造对立、制造选择焦虑、诱导你看完找答案。而真相是「不用选」，这个标题就没有张力了。

所以你会在网上看到大量 2024 年模板的对比文章和视频，它们不是故意骗你，很多是**内容生产者自己也没跟上版本**——LangChain 1.0 是 2025 年 10 月的事，之前的教程、书籍、博客全是旧范式。

> **一个可靠的过滤方法**：看到教程里写 `from langgraph.prebuilt import create_react_agent` 或者 `AgentExecutor`，直接关掉。这是「竞品叙事」时代的产物。

### 原因三（真正的答案）：因为做 AI 应用它们必须一起用

**这一条才是你真正该听的部分。**

一个生产级的 AI 应用，需要的完整能力是这些：

| 能力 | 谁提供 |
|---|---|
| 模型调用、provider 切换 | LangChain |
| 工具定义与调用 | LangChain |
| 提示词编排、输出结构化 | LangChain |
| 检索器、向量库集成 | LangChain |
| **把上面这些东西串成多步流程** | LangGraph |
| **让流程能暂停、恢复、能被人审批** | LangGraph |
| **出问题能回到现场看每一步** | LangGraph（+ LangSmith） |

**左边四行和右边三行不是可选项，是同一个应用的必需零件。** 所以视频把它们放一起讲是对的——**只是它们大多没讲清楚「为什么必须一起用」。**

---

## 第 1 章 联系的本质：一层封装，不是两个工具

### 1.1 用一句话说清

> **LangChain 是 LangGraph 的「预设配置」，LangGraph 是 LangChain 的「发动机」。**

用汽车来类比可能更直观：

| | LangChain 相当于 | LangGraph 相当于 |
|---|---|---|
| 你接触的频率 | 天天碰 | 出问题或要改装时才碰 |
| 它能做什么 | 开起来、转弯、加速 | 决定能跑多快、能不能跑长途 |
| 拆掉会怎样 | 车还能开（直接手写 LangGraph） | 车彻底不动 |

### 1.2 从代码上看这个联系

**你写的 LangChain 代码：**

```python
from langchain.agents import create_agent

agent = create_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[search_docs, query_db],
    system_prompt="你是一个技术文档助手。",
)

result = agent.invoke({"messages": [{"role": "user", "content": "怎么配置超时？"}]})
```

**它内部实际发生的事情**（概念上等价于）：

```python
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.postgres import PostgresSaver

class State(TypedDict):
    messages: Annotated[list, add_messages]

def model_node(state):
    return {"messages": [model.bind_tools(tools).invoke(state["messages"])]}

def should_continue(state):
    return "tools" if state["messages"][-1].tool_calls else END

g = StateGraph(State)
g.add_node("model", model_node)
g.add_node("tools", ToolNode(tools))
g.add_edge(START, "model")
g.add_conditional_edges("model", should_continue, {"tools": "tools", END: END})
g.add_edge("tools", "model")        # ← 这个环就是 Agent 循环

app = g.compile(checkpointer=PostgresSaver.from_conn_string(DB_URL))
```

**看懂这段对照，你就看懂了两者的全部联系：**

- `create_agent` 帮你自动生成了这个环
- Middleware 就是插在这个环的六个位置上的钩子
- 你写 `create_agent` 时觉得「没有 checkpointer 这个概念」——**是因为 LangChain 替你选了默认值**（开发时内存、生产时可配 Postgres）
- 一旦你要改这个环的形状（加一个审批节点、改成先分类再检索），**就是在写 LangGraph**

### 1.3 联系的三种具体形态

理解了封装关系，就能推导出两者交互的三种形态：

**形态一：LangChain 在上，LangGraph 在下面（最常见）**

```python
agent = create_agent(model="...", tools=[...])
```

你只需要 LangChain，LangGraph 隐式存在。**这是 90% 的场景。**

**形态二：LangGraph 在图里调 LangChain 的零件（很实用）**

```python
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool

model = init_chat_model("anthropic:claude-sonnet-4-6")   # LangChain 的模型抽象

@tool
def search_docs(q: str) -> str:                          # LangChain 的工具抽象
    """搜索文档。"""
    return retriever.invoke(q)

# 把 LangChain 的零件放进自己画的图
g.add_node("agent", lambda s: {"messages": [model.invoke(s["messages"])]})
```

**这是很多人的实际工作状态**：图是自己画的，但工具和模型仍然用 LangChain 的抽象（因为它们 provider 无关、生态最全）。**两者不是「用了这个就不能用那个」。**

**形态三：LangGraph 的节点里包一个完整的 LangChain Agent（多 Agent 常用）**

```python
# 每个子 agent 就是一个 create_agent 的产物
research_agent = create_agent(model, tools=[web_search, read_pdf])
analysis_agent = create_agent(model, tools=[run_python, query_sql])

# 它们作为节点被编排进更大的图
g.add_node("research", research_agent)
g.add_node("analysis", analysis_agent)
```

**这是多 Agent 系统的标准搭建方式**——每个专家 Agent 用 LangChain 快速搭好（不用手写循环），编排逻辑用 LangGraph 精确控制（谁调谁、什么时候停、什么时候要人审批）。

> **一句话总结三种形态**：LangChain 造零件，LangGraph 组装并管运行。你可以只用零件（形态一）、只用组装而零件外购（形态二）、或者造一堆小零件再组装成大家伙（形态三）。

### 1.4 用「能力对照」再确认一次

如果你还是觉得模糊，看这张表——**同一件事，两层各管什么**：

| 你想要的 | LangChain 层 | LangGraph 层 |
|---|---|---|
| 换个模型试试 | `init_chat_model("openai:gpt-5")` 一行搞定 | 不感知 |
| 让 Agent 能查数据库 | `@tool` 装饰器 + 塞进 `tools=[]` | 不感知 |
| 让 Agent 在敏感操作前暂停 | `HumanInTheLoopMiddleware` | `interrupt()` + checkpointer（底层实现） |
| 把长对话自动摘要 | `SummarizationMiddleware` | 不感知 |
| 控制「先分类、再检索、再生成、失败重试」 | 写不出来 ❌ | `add_conditional_edges` ✅ |
| 让流程中断 3 天后还能接着跑 | 写不出来 ❌ | Postgres checkpointer ✅ |
| 回放一次出错的运行看哪步错了 | 写不出来 ❌ | `get_state_history` ✅ |

**那些「写不出来」的行，就是你从 LangChain 降到 LangGraph 的信号。**

---

## 第 2 章 与 RAG 的联系（这一章对你最关键）

你在用 RAG 找工作。前面聊的都是 LangChain 与 LangGraph 之间的关系，**但你真正该关心的是：这两样东西和 RAG 是什么关系？**

### 2.1 先给结论

> **RAG 是一种「架构模式」，LangChain 和 LangGraph 是「实现这个模式的工具箱」。**
>
> **三者不在同一个层次上：RAG 是 what（做什么），LangChain/LangGraph 是 how（怎么做）。**

而且——**RAG 是 LangChain 最早、最经典的杀手级用例**。这是理解「为什么 RAG 教程里全是 LangChain」的钥匙。

### 2.2 用需求对映三层

把你的 RAG 学习手册里的技术点，和这两层的工具能力对一下：

| RAG 环节 | 谁来实现 |
|---|---|
| 文档加载（PDF / Word / 网页） | **LangChain 的 Document Loaders** |
| 文本分块 | **LangChain 的 Text Splitters** |
| 向量化 + 存向量库 | **LangChain 的 Embeddings + VectorStore 集成** |
| 检索（含 MMR、多查询） | **LangChain 的 Retriever 抽象**（`MultiQueryRetriever` 等） |
| 拼 Prompt + 生成 | **LangChain 的 Prompt + Model** |
| 评估（RAGAS 四指标） | **LangSmith / Langfuse 的 Evaluator** |
| **「检索质量不够 → 改写查询再检索」这个循环** | **LangGraph** ← 关键分界线 |
| **「查到的资料不相关 → 换数据源」这个分支** | **LangGraph** |
| **「Self-RAG 的反思令牌决定要不要再检索」** | **LangGraph** |
| **「多轮追问时保持会话状态」** | **LangGraph + checkpointer** |

**这条分界线非常重要**：

- **朴素的 RAG（Naive RAG）是一条直线**——加载、切块、存、查、拼、生成。**直线用 LangChain 就够了，不需要 LangGraph。**
- **一旦你的 RAG 出现「循环」和「分支」——也就是你手册里的 Advanced RAG、Self-RAG、CRAG、Agentic RAG——你就需要 LangGraph 了。**

### 2.3 这就是 RAG 六种流派与两层的对应关系

回顾你 RAG 手册里的六种流派，对照看需要哪一层：

| RAG 流派 | 流程形态 | 需要 LangGraph 吗 |
|---|---|---|
| **Naive RAG** | 直线 | ❌ 不需要，LangChain 一条链搞定 |
| **Advanced RAG** | 直线 + 前置/后置优化 | ❌ 大多不需要，用 LangChain 的 Retriever 组合 |
| **Modular RAG** | 可插拔模块组合 | ⚠️ 视情况，模块间有分支就需要 |
| **GraphRAG** | 建图 + 社区检测 + 查询路由 | ✅ **需要**，查询路由是典型条件边 |
| **Self-RAG** | 生成 ←→ 反思 ←→ 再检索 | ✅ **必须**，这是循环结构 |
| **Agentic RAG** | 协调者 + 多个专业检索 Agent | ✅ **必须**，多 Agent 拓扑 + 循环 |

**这张表就是回答「我学 RAG 要不要学 LangGraph」的完整答案。**

> **诚实的建议**：如果你只是做一个文档问答，**别上 LangGraph，也别看 Self-RAG**。你手册里那条主线「别一上来就做 Agentic RAG」是对的。但只要你的项目需要「检索不理想时自动换个策略重试」，LangGraph 就是绕不过去的。

### 2.4 落成代码看这个联系

**Naive RAG（纯 LangChain，不用 LangGraph）：**

```python
from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate

chain = (
    {"context": retriever | format_docs, "question": RunnablePassthrough()}
    | ChatPromptTemplate.from_template("根据以下资料回答：\n{context}\n\n问题：{question}")
    | init_chat_model("anthropic:claude-sonnet-4-6")
)

chain.invoke("怎么配置超时？")
```

**这就是 web 上最常见的 RAG demo。它是直线，所以优雅。**

**Self-RAG 风格（需要 LangGraph 才能表达那个循环）：**

```python
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.postgres import PostgresSaver

class RAGState(TypedDict):
    question: str
    rewritten_query: str
    docs: list
    answer: str
    retry_count: int

def retrieve(state):
    return {"docs": retriever.invoke(state["rewritten_query"])}

def grade_docs(state):
    """评估检索到的文档是否相关 —— 一个 LLM 调用。"""
    return {"relevance": judge(state["question"], state["docs"])}

def rewrite_query(state):
    """检索质量不够时改写查询再试一次。"""
    return {
        "rewritten_query": rewrite_llm.invoke(state["question"]).content,
        "retry_count": state["retry_count"] + 1,
    }

def generate(state):
    return {"answer": gen_llm.invoke(build_prompt(state)).content}

def route_after_grading(state) -> str:
    if state["relevance"] == "relevant":
        return "generate"
    if state["retry_count"] >= 2:              # ← 必须有退出条件
        return "generate"                       # 放弃重试，用现有材料回答
    return "rewrite"

g = StateGraph(RAGState)
g.add_node("retrieve", retrieve)
g.add_node("rewrite", rewrite_query)
g.add_node("generate", generate)

g.add_edge(START, "retrieve")
g.add_conditional_edges("retrieve", route_after_grading,
                        {"generate": "generate", "rewrite": "rewrite"})
g.add_edge("rewrite", "retrieve")               # ← 这个环就是 Self-RAG 的本质
g.add_edge("generate", END)

app = g.compile(checkpointer=PostgresSaver.from_conn_string(DB_URL))
```

**把这个例子和上面的 Naive RAG 对照着看，你就彻底理解了两者的联系**：

- 差别只在**那一条 `add_edge("rewrite", "retrieve")` 的回边**
- 那一条回边就是「检索不好就重来」
- 但有了回边，就带来了递归上限、状态管理、退出条件这些新问题——**这就是为什么它需要 LangGraph 而不是 LangChain 的链**

> **面试时可以直接这么讲**：「朴素 RAG 是直线，用 LCEL 链就够。但我要做的是带检索质量评估的自我纠错 RAG，需要在检索和改写之间循环，这个循环用 LangGraph 的状态图表达最自然——同时我还给重试加了次数上限，避免无限循环。」**这句话同时证明了你会 RAG、会 LangGraph、还知道它们各自的适用边界。**

### 2.5 反过来：LangGraph 让 RAG 从「能答」到「可靠」

这一层联系对面试特别有价值。**LangGraph 给 RAG 带来的不只是「能循环」：**

| LangGraph 能力 | 对 RAG 的意义 |
|---|---|
| **Checkpointer** | 多轮追问时不用重复检索已有上下文；会话状态跨请求保持 |
| **interrupt** | 检索到低置信度内容时，让人工确认再生成（金融、医疗场景刚需）|
| **条件边** | 查询路由：技术问题走文档库、数据问题走 SQL、闲聊走通用模型 |
| **子图** | 每个数据源一个独立的检索子 Agent，各自有独立的检索策略 |
| **Streaming** | 显示「正在检索…找到 5 篇文档…正在生成」的进度反馈 |
| **Time travel** | 用户投诉「你上次答错了」，回放那次运行看到底检索到了什么 |

**最后那一行是杀手级能力**：RAG 系统最痛的调试场景就是「这个答案为什么是错的」。纯链式实现你只能重跑一次看运气；有了 checkpoint，你能精确回到那次运行，看到**当时检索到的究竟是哪几篇文档**。

---

## 第 3 章 三者关系总图

把前面所有内容压缩成一张图，这张图建议记住：

```
┌─────────────────────────────────────────────────────────────┐
│                     目标层：要做什么                          │
│                                                              │
│   RAG（检索增强生成）        Agent（工具调用循环）             │
│   直连模型 / 微调 / ...      数据分析 / 自动化 / ...           │
└────────────────────────┬────────────────────────────────────┘
                         │ 用什么实现
                         ↓
┌─────────────────────────────────────────────────────────────┐
│                 模式层：用什么结构                            │
│                                                              │
│   直线流程（Chain）          循环与分支（Graph）               │
│   · Naive RAG               · Self-RAG / CRAG               │
│   · 单轮问答                 · Agentic RAG                   │
│   · 固定 pipeline            · 多 Agent 协作                 │
└────────────────────────┬────────────────────────────────────┘
                         │ 用什么实现
                         ↓
┌─────────────────────────────────────────────────────────────┐
│                 工具层：用什么写代码                          │
│                                                              │
│   ┌───────────────────────┐    ┌─────────────────────────┐  │
│   │  LangChain            │    │  LangGraph              │  │
│   │  · 模型与工具抽象      │    │  · StateGraph 运行时     │  │
│   │  · Loaders/Splitters  │    │  · Checkpointer         │  │
│   │  · Retriever 抽象     │    │  · interrupt            │  │
│   │  · create_agent       │    │  · 条件边 / 子图         │  │
│   │  · Middleware         │    │  · Streaming            │  │
│   └───────────────────────┘    └─────────────────────────┘  │
│                                                              │
│         ↑ 造零件                      ↑ 组装并管运行          │
└─────────────────────────────────────────────────────────────┘
```

**读图方法（从上往下问三个问题）**：

1. **我要做什么？** → 决定是 RAG 还是 Agent 还是别的
2. **它是什么形状？直线还是循环？** → 直线用 LangChain 链，循环用 LangGraph 图
3. **那我要写哪一层的代码？** → 直线：只写 LangChain；循环：LangChain 造零件 + LangGraph 组装

> **最关键的判断在第 2 步。** 很多人跳过它直接跳到第 3 步去问「该学哪个」，所以永远想不清楚。**形状决定工具，不是热度决定工具。**

---

## 第 4 章 五组最容易混淆的组合辨析

视频把它们放一起讲，经常把这几个东西说混。逐个拆开。

### 4.1 LangChain 的 Chain vs LangGraph 的 Graph

| | Chain（LCEL） | Graph（StateGraph） |
|---|---|---|
| 形状 | 有向无环，**直线** | 可以有环、可以有分支 |
| 数据流 | 上一个的输出是下一个的输入 | **共享状态**，每个节点读写自己需要的字段 |
| 状态 | 不保留 | 每个 superstep 持久化 |
| 能否暂停 | ❌ | ✅ interrupt |
| 能否恢复 | ❌ | ✅ checkpointer |
| 适合 | 固定的数据加工管线 | 需要决策、循环、人机协同的流程 |

**关键区别是「数据流」那一行。** Chain 是**管道**（output 传给下一个 input），Graph 是**黑板**（所有节点往同一块状态上读写）。这就是为什么 Graph 能做循环而 Chain 不能——循环需要一个持续存在的状态，而不是一进一出。

> 顺带说：Chain 的经典代表 `LCEL`（`|` 运算符）在 1.0 之后属于 `langchain-classic` 定位。**不是说它不好，而是说它解决的「直线数据流」问题，用 Graph 也能做，官方现在主推统一到 Graph 运行时上。**

### 4.2 Retriever vs VectorStore vs RAG

这三个词经常被混着说：

| 名词 | 是什么 | 层次 |
|---|---|---|
| **VectorStore** | 存向量并做相似度搜索的**存储** | 基础设施 |
| **Retriever** | 一个**接口**：输入 query，返回文档列表 | LangChain 的抽象 |
| **RAG** | 一个**架构模式**：检索 + 生成 | 架构 |

**关系是**：VectorStore 是 Retriever 的一种常见实现（还有 BM25 Retriever、Web Retriever、Parent-Document Retriever 等）；Retriever 是 RAG 的一个环节。

**LangChain 在这里的价值**是它把 Retriever 定义成了一个统一接口——`retriever.invoke(query)`，于是**你可以把任何一种检索方式无缝替换或组合**，而且下游的 prompt 拼接代码完全不用改。这个抽象看着简单，但它是「混合检索」「多路召回」能轻松实现的基础。

### 4.3 Agent vs RAG

这两者**不是对立关系**，很多人搞错：

| | RAG | Agent |
|---|---|---|
| 干什么 | 给模型喂对上下文 | 让模型多步执行任务 |
| 核心动作 | 检索 | 决策 + 调工具 |
| 循环 | 通常无 | **本质上有**（思考-行动-观察） |

**它们可以叠加**：Agentic RAG 就是「Agent 把检索当成一种工具来用」，模型自己决定要不要检索、检索几次、检索什么。

> **面试加分答法**：「RAG 和 Agent 不是二选一。RAG 解决的是知识注入问题，Agent 解决的是任务编排问题。我们的做法是把检索封装成一个工具交给 Agent，让模型自己判断是否需要检索——这就是 Agentic RAG 的本质。」

### 4.4 LangGraph vs 普通 Python 状态机

**LangGraph 本质上就是一个状态机框架，但它比手写状态机多了三样东西**：

1. **Checkpointer**：状态自动持久化，崩溃能恢复（手写要自己搞序列化和存储）
2. **interrupt**：一等公民的暂停/恢复原语（手写要自己搞协程或消息队列）
3. **Streaming**：多模式流式输出（手写要自己设计事件协议）

**所以「我可以用 Python 写个 while 循环加状态字典」是对的**——直到你需要上面这三样。这也是为什么官方的建议是「控制流成为问题时才上 LangGraph」，而不是「所有 Agent 都必须用」。

### 4.5 Middleware vs Node

1.0 之后新增的混淆点：

| | Middleware | Node |
|---|---|---|
| 挂在哪 | Agent 循环的**固定六个钩子点** | 图的**任意位置** |
| 粒度 | 横切（脱敏、摘要、审计、重试） | 具体业务逻辑 |
| 能改流程吗 | 能拦截，但**不改变图的拓扑** | **就是拓扑本身** |
| 谁定义 | LangChain 定义钩子点 | 你自己画图 |

**判断口诀**：
- 「所有工具调用都要加审计」→ **Middleware**（横切关注点）
- 「只有高风险工具调用的前后要加审批节点」→ **Node**（特定拓扑位置）

---

## 第 5 章 学习顺序建议（针对你的情况）

你现在手上已经有三份手册了。**别打算平行学，按下面这个顺序。**

### 5.1 推荐顺序

```
第 1 步：《RAG 系统学习手册》
        ↓ 先把「检索」这件事吃透 —— 这是你的求职方向
第 2 步：《LangChain 与 LangGraph 系统手册》第 1、2 章
        ↓ 把「竞品」认知彻底清掉，学会 create_agent
第 3 步：本文（关系手册）
        ↓ 建立三层视角，看懂工具与目标的关系
第 4 步：《RAG 与 LangChain / LangGraph 联系手册》
        ↓ 搞清楚三件套的真相、什么形状的 RAG 需要哪一层
第 5 步：回到《LangChain 与 LangGraph 系统手册》第 3-7 章
        ↓ 需要画图时再学 LangGraph，按需深入
```

### 5.2 为什么是这个顺序

**先 RAG 后框架**，理由有三：

1. **RAG 是你的求职方向**，LangChain 只是实现工具。面试官问「为什么用混合检索」，答案在检索原理里，不在框架文档里。
2. **你手册里那句话是对的**——「张口就是 LangChain / LlamaIndex 封装」是减分项。**先有原理判断力，再学工具，你才不会被工具牵着走。**
3. **框架会变，原理不会变。** 你手册里写过 1.0 之后 `create_react_agent` 被弃用、chains 搬进 `langchain-classic`——两年后可能还有别的变化。但「稠密检索在编号场景必然失败」这个判断不会变。

### 5.3 一个可以立刻做的整合练习

**把两份手册串起来做一个项目**，这是最高效的学法：

> **做「错误码排查助手」，但用 LangGraph 实现带自我纠错的检索。**

为什么推荐这个组合（理由来自你 RAG 手册里的建议）：

1. **错误码场景下纯向量检索必然失败** —— 这是你手册里的重点论点，因为「E1234」这种编号在向量空间里没有语义相近性。你可以真实地展示「加 BM25 混合检索后召回率提升」的对比数据。
2. **纠错循环天然需要 LangGraph** —— 「检索到的错误码文档不匹配 → 改写查询重试」，这个循环正好用上第 2.4 节那段代码。
3. **面试时能讲出完整故事**：「我遇到的问题是 X，纯向量方案失败，我加了 Y 策略，用 LangGraph 表达重试循环，评估指标从 A 提升到 B。」

**这个项目同时证明了你会 RAG、会 LangGraph、还会做效果量化——三件事一次讲清。**

### 5.4 别做的事

| 别做 | 原因 |
|---|---|
| 平行学三份手册 | 会陷入「什么都知道一点，什么都说不清」 |
| 先学完 LangGraph 全部特性再动手 | 你会花两周学用不上的 API（Swarm、Functional API 等） |
| 为了用 LangGraph 而用 LangGraph | 简单 RAG 用链就够，强行上图是过度设计，面试会被问倒 |
| 只学框架不学原理 | 「你用 LangChain 搭了个问答机器人」是典型减分回答 |

---

## 第 6 章 一页速查

### 6.1 关系一句话

| 问题 | 答案 |
|---|---|
| LangChain 和 LangGraph 什么关系？ | 同一栈的上下两层，LangChain 构建其上，不是竞品 |
| 为什么会一起讲？ | 因为一套栈本来就该一起讲；也因为有流量 |
| 该学哪个？ | 从 LangChain 起手，控制流成为瓶颈时降到 LangGraph |
| 怎么判断要不要降？ | 直线用 LangChain，**循环/分支/持久化/人工审批**用 LangGraph |
| 它们和 RAG 什么关系？ | RAG 是要做什么，它们是**怎么做**；Naive RAG 只需 LangChain，Self-RAG/Agentic RAG 必须 LangGraph |
| 能不能只用 LangGraph？ | 能，但工具和模型抽象还是用 LangChain 的省事（形态二） |
| 能不能只用 LangChain？ | 能，只要你的流程是直的、不需要暂停恢复 |

### 6.2 判断流程图（贴墙版）

```
你的需求是什么形状？
│
├─ 直线（固定几步，无分支）
│     → 用 LangChain 链 / create_agent
│     → 例子：Naive RAG、单轮问答、固定 ETL 管线
│
├─ 直线 + 人审批（中间停一下）
│     → 先用 Middleware（HITL）试试，可能不用画图
│
└─ 有循环 / 有分支 / 要持久化 / 要回放
      → 用 LangGraph
      → 例子：Self-RAG、Agentic RAG、多 Agent、长时工作流
  
⚠️ 但先问自己：这个循环/分支，是业务真的需要，
   还是我为了用框架而设计的？
```

### 6.3 配套手册定位

| 手册 | 回答的问题 | 什么时候翻 |
|---|---|---|
| **《RAG 系统学习手册》** | 检索怎么做才有效 | 设计检索策略、准备 RAG 面试 |
| **《LangChain 与 LangGraph 系统手册》** | 用什么工具、怎么避坑 | 写代码、排查生产问题 |
| **本文（关系手册）** | LangChain 与 LangGraph 之间怎么连 | 做技术选型、面试被问两者关系 |
| **《RAG 与 LangChain / LangGraph 联系手册》** | RAG 与这两者怎么连、三件套怎么理解 | 定项目架构、面试被问「为什么用这个」 |

**一句话的配合逻辑**：**原理看第一份，工具看第二份，层次关系看后两份。面试的核心永远是原理加取舍，工具只是载体。**

---

## 附录：常见疑问快答

**Q：既然 LangChain 建在 LangGraph 上，是不是学 LangGraph 就够了？**

不行，反过来说更准确。LangGraph 只给你原语（状态、节点、边、checkpointer），**模型抽象、工具定义、文档加载、Retriever、Prompt 模板这些东西 LangGraph 不提供**。你还是得用 LangChain 的（或用别的库自己实现）。官方那句「用 LangChain 不需要懂 LangGraph」的镜像版本是：**用 LangGraph 几乎总会用到 LangChain 的零件。**

**Q：网上说「LangGraph 是 LangChain 的替代品」，对吗？**

错，而且是最典型的过时说法。它们根本不是同类东西——一个是 Agent 构建框架，一个是编排运行时。替代关系不成立。

**Q：我用 LCEL 写了 RAG 链，需要改成 LangGraph 吗？**

**先别改。** 问你自己：这条链需要循环吗？需要中途暂停吗？需要跨请求保持状态吗？需要回放历史运行吗？**四个都是「不需要」，就别动它。** 为了用新框架重写能跑的代码，是典型的过度工程。等真的需要那个循环时再迁。

**Q：RAG 项目面试，LangChain 会问到多深？**

按你 RAG 手册里那条判断——「张口就是 LangChain 封装」是减分项——可以反推出期望：**面试官希望你把框架当实现细节讲，而不是当卖点讲。** 比较好的表达是：「检索这块我用了 LangChain 的 Retriever 抽象来统一多路召回，因为它的接口设计让混合检索的组合成本很低；但 BM25 的 k1、b 参数和 RRF 的融合策略是我自己调的。」**先讲原理和取舍，框架在句尾带过。**

**Q：LangGraph 值得为求职专门学吗？**

看你的目标岗位。**如果岗位 JD 里出现「Agent」「工作流编排」「多智能体」，值得。** 如果只是「RAG 应用开发」，那**优先级是 RAG 深度 > LangChain 熟练 > LangGraph 了解**——能讲清楚「什么时候需要从链升级到图」比会写复杂的图更值钱。

---

## 参考来源

- LangChain 官方博客《LangChain and LangGraph Agent Frameworks Reach v1.0 Milestones》（2025-10）
- LangChain 官方文档 docs.langchain.com（v1 迁移指南、LangGraph 概念文档、Middleware 文档）
- LangGraph 官方文档（StateGraph、Checkpointer、Human-in-the-Loop、Subgraph 章节）
- 与本仓库另两份手册交叉引用：《RAG 系统学习手册》第 1 章（六种流派）、第 10 章（面试准备）；《LangChain 与 LangGraph 系统手册》第 1 章（生态分层）、第 3 章（三原语）、第 6 章（选型决策）

**时效性说明**：LangChain / LangGraph 生态演进快（1.0 发布于 2025-10，2026-08 已到 1.2.x）。本手册的关系判断建立在 1.0 之后的架构上——**如果未来官方又把两者拆开或合并，第 1 章的分层结论需要重新核实**，但第 2 章的「RAG 是目标、框架是工具」这层关系不受影响。
