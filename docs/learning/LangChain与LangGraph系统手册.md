# LangChain 与 LangGraph 系统手册

> 面向「被网上碎片信息绕晕」的开发者，从定位辨析到面试考点的一次性打通。
>
> 编写时间：2026 年 9 月。技术版本基准：LangChain 1.x（1.0 于 2025-10-22 GA，1.1 于 2025-12-02）、LangGraph 1.x（PyPI 2026-05-04 显示 langgraph 1.2.11，langchain-core 1.5.3）。

> **急着查具体内容？** 跳到 [定位目录](#定位目录) 按需求直达章节。建议先读下面的开篇，它会纠正一个普遍误解。

---

## 写在最前面：你可能正被一个错误的认知困住

你说「了解到的很片面」——这不是你的问题，是信息环境的问题。

网上绝大多数「LangChain vs LangGraph」的对比文章，写的是 **2024 年以前的故事**。那个年代，LangChain 是「链式编排 + 一堆集成」，LangGraph 是「用图来做循环的另一个库」，两者看起来确实像竞品，文章标题也就自然地写成了「该选哪个」。

**但 2025 年 10 月 22 日之后，这个叙事彻底作废了。**

LangChain 1.0 与 LangGraph 1.0 在同一天发布（前者 10 月 22 日，后者 10 月 23 日公告），官方博客里有一句定位原话，请把它背下来：

> **"LangChain is the agent framework: abstractions and integrations for models, tools, and agent loops. LangGraph is the orchestration runtime: durable execution, streaming, human-in-the-loop, and persistence."**
> —— LangChain 官方博客，2025 年 10 月

翻译过来就是：

| | 定位 | 一句话比喻 |
|---|---|---|
| **LangChain** | Agent 构建框架（上层） | 汽车的**方向盘与仪表盘**，你直接摸的地方 |
| **LangGraph** | 编排运行时（下层） | 汽车的**底盘与传动系统**，决定车能不能跑长途、能不能拐弯 |

**关键事实：LangChain 1.0 的 `create_agent` 底层就是在 LangGraph 运行时上执行的。** 你写八行代码建一个 Agent，脚下踩的已经是 LangGraph 的持久化执行引擎。官方博客的原话是：

> "The new `create_agent` function uses LangGraph under the hood to run this loop. It has a very similar feel to the `create_react_agent` function from `langgraph.prebuilts`."

同时官方也体贴地补了一句，用来消除新手的心理负担：

> **"While LangChain is built on top of LangGraph, you don't need to know LangGraph to use LangChain."**

（虽然 LangChain 构建在 LangGraph 之上，但用 LangChain 并不需要你懂 LangGraph。）

### 所以正确的理解姿势是

```
你写的业务代码
      ↓
  LangChain 1.x          ← 高层 API：create_agent、tools、middleware、structured output
      ↓
  LangGraph 1.x          ← 低层运行时：StateGraph、checkpointer、interrupt、streaming
      ↓
你的基础设施（Postgres / Redis / K8s）
```

**不是二选一，是在同一根栈上选择「站在哪一层」。** 这就像问「我应该用 Django 还是用 Python」——问法本身就错了。Django 是 Python 的框架，LangChain 是 LangGraph 之上的一层抽象。你说「我要不要学 LangGraph」，真实的含义其实是：**我的业务复杂到需要直接操作运行时了吗？**

后面第 6 章会给你一张明确的选型决策表。

### 关于「片面认知」的其他三个常见误区

在正式展开前，先把网上流传最广的三个错误说法一次性纠正掉：

**误区一：「LangChain 只是个调 API 的 wrapper，没什么技术含量」**

这是 2023 年的印象。1.0 之后 LangChain 的核心价值变成了三件事：① `create_agent` 提供标准化的 Agent 循环；② **Middleware 机制**让横切关注点（PII 脱敏、摘要压缩、人工审批、重试）变成可组合的插件而非散落的包装代码；③ **Standard content blocks** 用一套 provider-agnostic 的结构统一了各家模型的推理链、引用、工具调用输出。第三点在多模型切换场景下的价值被严重低估。

**误区二：「`create_react_agent` 是最佳实践」**

**它已经过时了。** `langgraph.prebuilt.create_react_agent` 和更老的 `AgentExecutor` 都被官方的 `create_agent` 取代。新代码的正确导入路径是：

```python
from langchain.agents import create_agent   # ✅ 1.0 之后的标准写法
```

如果你看到的教程还在写 `from langgraph.prebuilt import create_react_agent`，那篇文章至少落后了一年。

**误区三：「LangChain 和 LangGraph 包都要装，很臃肿」**

1.0 做了一次大瘦身。遗留的 chains、老的 retrievers（如 `MultiQueryRetriever`）、indexing API、`langchain-community` 的大量导出，全部搬到了独立的 **`langchain-classic`** 包。`langchain` 核心命名空间现在只专注于 Agent 构建。这也是「升级不是无痛的」这句话的来源——你的老 import 路径需要迁移。

---

## 定位目录

**按需求快速定位：**

| 你想知道 | 直接看 |
|---|---|
| **LangChain 和 LangGraph 到底是不是竞品** | [开篇](#写在最前面你可能正被一个错误的认知困住) |
| 生态里有哪几个产品、各自管什么 | [第 1 章](#第-1-章-生态全景图五件套各管什么) |
| **create_agent 怎么用、Middleware 是什么** | [第 2 章](#第-2-章-langchain-10create_agent-与-middleware) |
| LangGraph 的状态/节点/边怎么用 | [第 3 章](#第-3-章-langgraph-核心概念状态节点边) |
| **持久化、人工审批、多 Agent 怎么做** | [第 4 章](#第-4-章-langgraph-进阶持久化人机协同多-agent) |
| MCP / A2A / LangSmith 是什么 | [第 5 章](#第-5-章-强相关技术mcpa2alangsmith) |
| **我该用哪个、什么时候升级** | [第 6 章](#第-6-章-选型决策什么时候用哪个) |
| **生产上会踩哪些坑** | [第 7 章](#第-7-章-生产实战五个必然踩的坑与检查清单) |
| 完整可运行代码 | [第 8 章](#第-8-章-完整代码实战) |
| **面试考点、答法、减分项** | [第 9 章](#第-9-章-面试考点与答法) |
| 学习路线与误区清单 | [第 10 章](#第-10-章-学习路线与误区清单) |
| 术语释义 | [附录](#附录术语表) |

**完整章节：**

- [第 1 章 生态全景图：五件套各管什么](#第-1-章-生态全景图五件套各管什么)
- [第 2 章 LangChain 1.0：create_agent 与 Middleware](#第-2-章-langchain-10create_agent-与-middleware)
- [第 3 章 LangGraph 核心概念：状态、节点、边](#第-3-章-langgraph-核心概念状态节点边)
- [第 4 章 LangGraph 进阶：持久化、人机协同、多 Agent](#第-4-章-langgraph-进阶持久化人机协同多-agent)
- [第 5 章 强相关技术：MCP、A2A、LangSmith](#第-5-章-强相关技术mcpa2alangsmith)
- [第 6 章 选型决策：什么时候用哪个](#第-6-章-选型决策什么时候用哪个)
- [第 7 章 生产实战：五个必然踩的坑与检查清单](#第-7-章-生产实战五个必然踩的坑与检查清单)
- [第 8 章 完整代码实战](#第-8-章-完整代码实战)
- [第 9 章 面试考点与答法](#第-9-章-面试考点与答法)
- [第 10 章 学习路线与误区清单](#第-10-章-学习路线与误区清单)
- [附录：术语表](#附录术语表)

---

## 第 1 章 生态全景图：五件套各管什么

LangChain 官方现在把产品线（2025 年 10 月做过一次产品改名，LangGraph Platform 在部分文档里也叫 **LangSmith Deployment**，是同一个东西的双名过渡期）分成了清晰的三层。

### 1.1 分层图

```
┌─────────────────────────────────────────────────────────────┐
│  第 3 层：可观测与部署                                        │
│                                                              │
│  ┌──────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │  LangSmith   │  │ LangGraph        │  │  LangServe    │  │
│  │  观测/评估    │  │ Platform         │  │  (旧方案)      │  │
│  │  /Prompt管理  │  │ (托管部署/任务队列│  │  暴露 HTTP API │  │
│  │              │  │  /cron/扩缩容)    │  │               │  │
│  └──────────────┘  └──────────────────┘  └───────────────┘  │
│        闭源 SaaS       闭源 SaaS（有自托管档）   开源但已不推荐  │
├─────────────────────────────────────────────────────────────┤
│  第 2 层：Agent 构建（高层 API）                              │
│                                                              │
│   LangChain 1.x                                              │
│   · create_agent        标准 Agent 循环（8 行代码起手）        │
│   · Middleware          六钩子横切机制                        │
│   · Standard content    跨 provider 统一消息结构              │
│   · tools / models      模型与工具集成层                      │
│   langchain-classic     ← 遗留功能的收容所（chains 等）        │
├─────────────────────────────────────────────────────────────┤
│  第 1 层：编排运行时（低层）                                   │
│                                                              │
│   LangGraph 1.x                                              │
│   · StateGraph          状态机建模                            │
│   · Checkpointer        持久化执行（Postgres/SQLite/Redis/内存）│
│   · interrupt / Command 人机协同                              │
│   · Streaming           多模式流式输出                        │
│   · Subgraphs           层级式多 Agent                        │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 逐个说清楚

**LangGraph（开源，MIT）**
低层编排框架与运行时。官方定义它提供四大核心能力：**持久执行**（durable execution，服务器重启后从中断点自动恢复）、**人机协同**（Human-in-the-Loop，任意时点检查并修改状态）、**全面记忆**（短期工作记忆 + 跨会话长期记忆）、**生产级部署**（配合平台层）。宣传语是「为*任何*长时间运行、有状态的工作流或 Agent 提供底层基础设施」——注意「不抽象提示和架构」，它只给你原语，不替你决定结构。

**LangChain（开源，MIT）**
高层 Agent 构建框架。1.0 的四大变化：`create_agent`、Middleware、Standard content blocks、命名空间瘦身。官方对它的定位是「构建 AI Agent 最快的方式」——标准化工具调用架构、provider 无关设计、Middleware 可定制。

**LangSmith（闭源 SaaS）**
可观测性、追踪、评估、Prompt 管理。LangChain 本身提供了底层的 callbacks 和埋点钩子，LangSmith 把它们包装成一个可视化界面。**一个 trace 能把多次模型调用、工具调用、检索步骤、Middleware 执行、中间输出聚合成一条可调试的记录。** 免费档 5,000 base traces/月、1 个席位；Plus 档 $39/席/月含 10,000 traces，超出部分 $2.50/千条（14 天保留）或 $5/千条（400 天保留）。

> 成本提醒：按这个定价，100 万 traces/月大约要 $2,514（单席位）。这是很多团队后期迁移到 Langfuse 的主要原因。

**LangGraph Platform / LangSmith Deployment（闭源 SaaS，有自托管档）**
托管运行 Agent 的基础设施：服务器、持久化、任务队列都替你管。档位包括：全托管 Cloud SaaS、免费 Self-Hosted Lite（上限 100 万次节点执行）、BYOC（跑在你自己的 VPC 里）、以及完整 on-prem 的 Enterprise 档。

CLI 入口：

```bash
langgraph dev      # 本地热重载服务器，不需要 Docker（Python 3.11+）
langgraph up       # 本地通过 Docker Compose 起全栈（Postgres + Redis）
langgraph build    # 产出可到处运行的 Docker 镜像
langgraph deploy   # 推送到 LangGraph Platform（截至 2026 年中期仍是 beta）
```

> **两个容易踩的坑**：① `langgraph deploy` 还是 beta，别急着把发布流水线建在它上面。② 独立服务器启动时**仍需要 LangSmith license key**（`LANGGRAPH_CLOUD_LICENSE_KEY`），Self-Hosted Lite 免费到 100 万次节点执行。但**发送 trace 到 LangSmith 是可选的**——不设 `LANGSMITH_API_KEY` 就不上报任何数据。

**LangServe（开源）**
把 LangChain Runnable（chain、检索流水线、Agent）暴露成 HTTP API 端点的 Python 工具，支持输入校验、异步执行、流式输出，能省掉不少 FastAPI 样板代码。**但官方现在建议新项目改用 LangGraph Platform**，加上它有自己的 license 问题，作为默认方案已经不太合适。老项目自管理 Python 服务时它还有用。

### 1.3 三条部署路径（很重要，别只知道一条）

这是 2026 年实践者总结出来的，教科书里通常不写：

| 路径 | 做法 | 适用场景 | 代价 |
|---|---|---|---|
| **托管 Platform** | `langgraph deploy` 推到 LangGraph Platform | 想要持久化 + cron 调度 + 任务队列，且愿意付费 | 费用 + 闭源锁定 |
| **Docker 自托管** | `langgraph build` 出镜像，跑在自己的 K8s/ECS/VM 上，配自己的 Postgres + Redis | 想要 LangGraph 运行时特性但跑在自己基础设施上 | 需要 LangSmith license key；要自己运维数据库 |
| **纯服务器（plain server）** | **不部署 LangGraph API server**，直接把编译好的 graph 用 FastAPI 路由包一层，容器化发布 | Agent 只是大应用里的一个功能；已有部署体系；想彻底去掉 LangSmith 依赖 | 没有 LangGraph 运行时的内置特性（任务队列等） |

**第三点最容易被忽略，但对大多数团队最实用。** 一个用 `create_agent` 或 LangGraph 编译出来的 agent，本质上就是个有 `invoke` 和 `stream` 方法的对象。塞进 FastAPI 路由、Next.js API handler 或 NestJS controller，像普通服务一样容器化发布就行——**这条路径连 license key 都不需要**。

自托管时组件职责要清楚：**Postgres 存 assistants、threads、run state 和任务队列；Redis 是流式输出的 pub-sub broker。**

### 1.4 观测层的开源替代方案

如果你不想被 LangSmith 的按量计费绑住，或者有数据合规要求：

| 工具 | License | 免费额度 | 特点 |
|---|---|---|---|
| **Langfuse** | MIT（核心） | 自托管免费无限；云版 50k units/月 | 功能最接近 LangSmith，2026 年 1 月被 ClickHouse 收购后核心仍是 MIT |
| **Helicone** | Apache-2.0 | 100k 请求/月 | 改个 base URL 就有 dashboard，零埋点 |
| **Arize Phoenix** | Elastic License 2.0 | 自托管免费，无事件上限 | OpenTelemetry 原生，本地开发轻量，嵌入漂移分析强 |
| **Braintrust** | 闭源 | $10 额度起步 | 离线评估与实验工作流 |
| **W&B Weave** | 闭源 | 1 GB/月 | 团队已在用 Weights & Biases 时选它 |

**Langfuse 的集成方式极简**，对已经用 LangChain/LangGraph 的项目几乎是零改造：

```python
from langfuse.langchain import CallbackHandler

agent.invoke(
    {"messages": [{"role": "user", "content": "Plan a 3-day trip to Lisbon"}]},
    config={"callbacks": [CallbackHandler()]},
)
```

**一个重要判断**：LangChain 会发出 OpenTelemetry 兼容的遥测数据，所以你可以把 trace 路由到 Langfuse，也可以路由到任何 OTel 后端（Jaeger、Grafana Tempo、Datadog）。**OpenInference** 是 Arize 主导的 OpenTelemetry 语义约定规范（标准化了 `llm.model_name`、`llm.token_count.completion` 这类 span 属性名），按它埋一次点，后续换后端不用重新埋点。

**另一个必须知道的判断**：

> 如果你已经有 Datadog 或 New Relic，**你还是需要 LLM 可观测性工具**。APM 工具擅长 HTTP 层延迟和错误，但它们不原生理解 prompt、completion、tool call 或 LLM-as-judge 评分。

实践中，把 OpenInference span 同时导出到一个通用 APM（做基础设施关联）和一个 LLM 专用观测工具（做 prompt、成本、评估工作流）。

**最后一条运营经验，来自踩过坑的团队**：从第一天起就在调用点埋好 **session ID、user ID、prompt-version tag**。世界上最好的厂商也没法按你从未告知的维度切片数据。

---

## 第 2 章 LangChain 1.0：create_agent 与 Middleware

### 2.1 create_agent：核心 Agent 循环

`create_agent` 围绕核心 Agent 循环设计。这个循环是这样的：

```
1. Setup      选择模型，给它工具和 prompt
2. Execution  发送请求给模型
3. 模型响应    要么 → 工具调用（执行工具，结果加回对话）→ 回到第 2 步
               要么 → 最终答案 → 返回结果
4. Repeat
```

**最小可用示例（八行）：**

```python
from langchain.agents import create_agent

def get_weather(city: str) -> str:
    """Get weather for a given city."""
    return f"It's always sunny in {city}!"

agent = create_agent(
    model="openai:gpt-5",           # provider 字符串形式
    tools=[get_weather],
    system_prompt="Help the user by fetching the weather in their city.",
)

result = agent.invoke({
    "messages": [{"role": "user", "content": "what's the weather in SF?"}]
})
```

**更规范的写法**（用 `init_chat_model` + `@tool` 装饰器）：

```python
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.tools import tool

@tool
def get_account_health(account_id: str) -> dict:
    """Return account health metrics from the warehouse."""
    return {"mrr": 4200, "usage_score": 78, "ticket_count": 2}

model = init_chat_model("anthropic:claude-sonnet-4-6")

agent = create_agent(
    model=model,
    tools=[get_account_health],
    system_prompt="You are a CS analyst. Use tools to answer.",
)

result = agent.invoke({
    "messages": [{"role": "user", "content": "Is account 482 healthy?"}]
})
```

**`model` 参数支持 provider 字符串**（如 `"openai:gpt-5"`、`"anthropic:claude-sonnet-4-6"`），这是 provider-agnostic 设计的直接体现。LangChain 1.1 还加了 **model profiles**——Chat model 现在暴露 `.profile` 属性描述自身能力（是否支持结构化输出、function calling 等），数据源自开放的 models.dev 目录。摘要 Middleware 和原生结构化输出策略会用这些 profile 做更聪明的决策。

**结构化输出**通过 `response_format` 参数传入 Pydantic schema。1.0 的一个重要改进是**把结构化输出生成折进了主模型-工具循环**，省掉了老模式需要的额外模型调用，直接降低延迟和成本。

### 2.2 Middleware：create_agent 的定义性特性

官方原话值得注意：

> **"Most agent builders are highly restrictive in that they don't permit customization outside of this core loop. That's where `create_agent` stands out with our introduction of middleware."**

（大多数 Agent 构建工具限制很死，不允许在核心循环之外做定制。`create_agent` 的突出之处就在于引入了 Middleware。）

Middleware 定义了一组**钩子（hooks）**，让你能在 Agent 循环的每一步做细粒度控制——**而不是去继承子类**。

#### 六个钩子

| 钩子 | 执行时机 | 典型用途 |
|---|---|---|
| `before_agent` | Agent 启动前 | 加载长期记忆、初始化会话状态 |
| `before_model` | 每次调用模型前 | 更新 prompt、注入上下文、动态挑选工具 |
| `wrap_model_call` | 包裹模型调用本身 | 拦截请求（改参数、加缓存、切换模型、路由） |
| `wrap_tool_call` | 包裹工具执行 | 拦截工具执行（鉴权、限流、审计、mock） |
| `after_model` | 模型返回后 | 校验输出、解析结构、记录指标 |
| `after_agent` | Agent 结束后 | 保存结果、写回长期记忆、清理 |

> 这六个钩子的设计意图很直白：**把「跨切面关注点」从 Agent 主体里抽出来。** 以前你为了加一个 PII 脱敏，得把 Agent 包装一层甚至重写；现在写个 Middleware 插进去就行，而且能复用到所有 Agent。

#### 官方预置的 Middleware

**1. Human-in-the-loop（人工审批）**
在工具执行前暂停 Agent，让用户批准、编辑或拒绝工具调用。官方特别强调：**对与外部系统交互、发送通讯、执行敏感交易的 Agent 来说这是必需品。**

**2. Summarization（摘要压缩）**
消息历史接近上下文上限时自动压缩——保留最近消息不动，把更早的上下文摘要掉。防止 token 溢出错误，让长时间运行的会话保持可用。

**3. PII redaction（PII 脱敏）**
用模式匹配识别并抹除邮箱、电话、社保号等敏感信息，在内容传给模型**之前**处理。帮助满足隐私合规，防止用户数据意外暴露。

```python
from langchain.agents.middleware import (
    PIIMiddleware,
    SummarizationMiddleware,
    HumanInTheLoopMiddleware,
    ToolRetryMiddleware,          # 工具重试（带退避）
)
```

1.1 版本还加入了 **OpenAI moderation 作为 Middleware**，以及内置的模型重试带退避（built-in model retry with backoff）。

#### 自定义 Middleware：两种写法

**写法一：装饰器模式（简单场景）**

```python
from langchain.agents.middleware import before_model, after_model

@before_model
def inject_context(state, runtime):
    """在每次调用模型前注入当前时间等动态上下文。"""
    ...

@after_model
def log_tokens(state, runtime):
    """记录每次模型调用的 token 消耗。"""
    ...
```

**写法二：类模式（需要状态、需要组合时）**

```python
from langchain.agents.middleware import AgentMiddleware

class AuditMiddleware(AgentMiddleware):
    """把所有工具调用写入审计日志。"""

    def wrap_tool_call(self, request, handler):
        log_to_audit(request.tool_call)
        return handler(request)
```

**选型建议**：装饰器模式适合「单点、无状态」的小逻辑（加个日志、注个时间）；类模式适合需要持有配置、需要在多个钩子间共享状态、或者要发布给别人复用的场景。

### 2.3 Standard content blocks

新引入的 `content_blocks` 属性（TypeScript 里是 `contentBlocks`）用**一套 provider 无关的结构**暴露现代模型能力：推理链（reasoning traces）、引用（citations）、工具调用（tool calls）。

支持范围：`langchain-anthropic`、`langchain-aws`、`langchain-openai`、`langchain-google-genai`、`langchain-ollama`。

**为什么这东西重要**：以前你要读模型的推理链，得给每个 provider 写一套特殊分支（OpenAI 的 reasoning 字段、Anthropic 的 thinking block、Google 的又是另一个形状）。有了 `content_blocks`，处理推理和引用的代码不再需要按 provider 特判。**这是消息层的去锁定（reduce provider lock-in at the message layer）。**

### 2.4 langchain-classic：迁移的现实

1.0 把下面这些东西搬到了 `langchain-classic`：

- 老式 chains
- 老 retrievers（包括 `MultiQueryRetriever`）
- indexing API
- hub 模块
- `langchain-community` 的导出

**这意味着升级不是「无操作」（not a no-op）。** 官方的迁移建议很务实：

1. **新项目**：直接从 `create_agent` 起手，用 Middleware 加护栏、摘要、人工审批。
2. **存量项目**：先 pin 住当前版本，读官方迁移指南，然后预算时间——要么把 import 迁到 `langchain-classic`，要么重构到 `create_agent`。
3. **多 provider 应用**：采用 `content_blocks` 统一推理和引用处理；用 model profiles（1.1+）做能力探测，而不是硬编码。

---

## 第 3 章 LangGraph 核心概念：状态、节点、边

### 3.1 三大原语

LangGraph 把工作流建模成**图**：每个节点封装一个工作单元（调 LLM、调工具、跑代码），边决定执行流和状态转移。

#### 原语一：State（状态）

用一个 TypedDict（或 Pydantic 模型）定义图的共享状态。

```python
from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]   # ← reducer 语法
    step: int
```

**Reducer：LangGraph 最容易踩的坑，没有之一。**

`Annotated[list, add_messages]` 里的 `add_messages` 是 reducer 函数，它规定了「当节点返回对该字段的更新时，如何合并」。`add_messages` 的语义是**追加并去重**（按 message id）。

如果你把 `messages` 写成裸 `list`：

```python
class BadState(TypedDict):
    messages: list          # ❌ 每个节点返回的 messages 会整体覆盖历史
```

后果是 **Agent 会在运行中途「失忆」**——它只记得最后一个节点给的消息，之前所有对话历史被覆盖掉了。这是新手最常遇到的问题，症状极具迷惑性（「为什么我的 Agent 记不住刚说过的话」）。

**并行分支的静默数据丢失**是同一个坑的进阶版：当并行图分支同时更新同一个状态字段、而该字段的 reducer 没有为并发配置时，会发生**最后写入者获胜（last-write-wins）**的静默数据丢失。这类问题在本地开发时通常发现不了。

**另一个高级技巧**：`StateGraph` 支持用 `input` / `output` schema 限制节点能看/能写什么。

```python
graph = StateGraph(FullState, input=InputSchema, output=OutputSchema)
```

这个「作用域隔离」（scope what each node can see）能治好很多「下游节点乱改上游节点产出」的毛病——**但必须从项目一开始就规划，后期补是重写。**

#### 原语二：Node（节点）

节点是纯函数：接收 state，返回 state 更新（delta），不是返回完整 state。

```python
def agent_node(state: AgentState) -> dict:
    response = llm.invoke(state["messages"])
    return {"messages": [response]}      # 只返回增量
```

注意签名约定：**返回 dict（部分更新），而不是修改 state 后返回 state。** 这也意味着节点应该是无副作用的纯逻辑（副作用放在工具里）——虽然实践中很难完全做到，但朝这个方向写会让图可测试得多。

#### 原语三：Edge（边）

三种类型：

```python
from langgraph.graph import StateGraph, START, END

graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", ToolNode(tools))

# 1. 静态边：A 走完必走 B
graph.add_edge("tools", "agent")

# 2. 条件边：根据 router 函数返回值决定去哪
graph.add_conditional_edges(
    "agent",
    should_continue,                              # router 函数
    {"tools": "tools", END: END},                 # 显式映射表
)

# 3. 入口与出口
graph.set_entry_point("agent")   # 等价于 add_edge(START, "agent")
```

**Router 函数必须返回明确的、可枚举的值。** 这是「Router 幻觉」这个失败模式的根源：条件边 router 输出了允许枚举之外的东西（`"maybe_billing"`、`"billing."`、一个 JSON 对象、或者一段道歉文字），图就路由到不存在的边而**死锁**，或者更糟——掉到默认分支里静默做错事。

**症状**：测试能过，生产上边缘输入就挂。
**修复**：把 router 输出对着显式允许集合做校验；未知值路由到一个 recovery 节点重新用更紧的 prompt 追问；降低 router 模型的 temperature；模型客户端支持的话用结构化输出约束。

### 3.2 最小完整示例：手写一个 ReAct Agent

```python
from typing import Annotated, TypedDict
from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]

def agent_node(state: State) -> dict:
    response = llm.invoke(state["messages"])
    return {"messages": [response]}

def should_continue(state: State) -> str:
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END

tool_node = ToolNode(tools=[search_web, read_file])

graph = StateGraph(State)
graph.add_node("agent", agent_node)
graph.add_node("tools", tool_node)
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")          # ← 这个环就是 ReAct 的「循环」

app = graph.compile(checkpointer=MemorySaver())
```

**这 20 行代码就是 ReAct 模式的全貌。** 值得注意的设计点：

- `graph.add_edge("tools", "agent")` 构成了环——这是 Agent 能「思考-行动-再思考」的机制所在。同时**它也是递归超限风险的来源**。
- `ToolNode` 是预置节点，替你处理工具调用的分发和结果包装。

### 3.3 运行与流式输出

```python
config = {"configurable": {"thread_id": "user-42"}}

for event in app.stream(
    {"messages": [HumanMessage("find the Anthropic headquarters address")]},
    config,
    stream_mode="updates",
):
    print(event)
```

**`stream_mode` 的两种主要模式，用途完全不同：**

| 模式 | 产出 | 用途 |
|---|---|---|
| `"values"` | 每步之后的**完整状态** | 需要看全貌、调试状态转移 |
| `"updates"` | 每步的**增量**，形如 `{node_name: state_delta}` | 推给前端做「Agent 正在思考…调用 search_web…拿到结果…生成答案」的实时反馈 |

**`"updates"` 模式是构建良好 Agent UI 的关键。** 把每个 update 直接流给前端，用户就能看到 Agent 的思考过程而不是干等。

还有 `astream_events(version="v2")` 用于更细粒度的事件级流式输出。

---

## 第 4 章 LangGraph 进阶：持久化、人机协同、多 Agent

### 4.1 Checkpointer：从「能跑」到「能活」

**没有 checkpointer = 没有恢复、没有 interrupt、没有时间旅行。它的重要性被严重低估。**

```python
from langgraph.checkpoint.postgres import PostgresSaver

with PostgresSaver.from_conn_string("postgresql://...") as checkpointer:
    checkpointer.setup()
    app = graph.compile(checkpointer=checkpointer)
```

**四种后端选型（生产上最常见的错误就是选错 checkpointer）：**

| Checkpointer | 持久性 | 延迟 | 适用 |
|---|---|---|---|
| `MemorySaver` / `InMemorySaver` | 重启即丢 | 最低 | **仅测试**。生产用它是反模式 |
| `SqliteSaver` | 单机持久 | 低 | 单进程小工具，**不要**用在多 worker 环境 |
| `RedisSaver` | 持久 | 低 | 需要低延迟 + 已有 Redis 基建 |
| `PostgresSaver` | 持久 | 中 | **生产默认选它**。多 worker 共享状态的标准方案 |

官方对后端的原话是：「Checkpointers ship for Postgres, SQLite, Redis, and in-memory. MemorySaver is for tests. Anything that persists across restarts wants a real store.」

**thread_id 隔离会话：**

```python
config = {"configurable": {"thread_id": "user-123"}}
result = app.invoke({"account_id": "482"}, config=config)
```

**记忆要让跨调用生效，两个条件缺一不可**：compile 时有 checkpointer **且** 每次 invoke 的 config 里有 `thread_id`。少任何一个，每次调用都从空白开始——这是新手第二大困惑来源。

**thread_id 命名建议**：用 `{user_id}:{session_id}` 而不是裸的 user_id。裸 user_id 在多会话场景下会冲突（同一个用户的两次不同对话串成一条）。一 thread 对应用户-会话是标准映射；另有一个 `checkpoint_id` 用来定位特定历史状态做重放或分支。

### 4.2 Human-in-the-Loop：三种实现方式

**方式一：`interrupt_before` / `interrupt_after`（编译期声明）**

```python
app = graph.compile(
    checkpointer=memory,
    interrupt_before=["sensitive_action"],       # 节点运行前暂停
    interrupt_after=["draft_response"],           # 节点运行后暂停
    # 可以声明多个
)
```

```python
config = {"configurable": {"thread_id": "user-123"}}

# 跑到中断点
result = app.invoke({"input": "data"}, config)
print("Paused for review. Current state:", result)

# 人工审核中……可以修改状态
app.update_state(config, {"approved": True, "modified_content": "Updated by human"})

# 继续（传 None 表示从 checkpoint 继续）
final_result = app.invoke(None, config)
```

**区别要记清楚**：
- `interrupt_before`：节点**运行前**暂停——可以先改状态再放行
- `interrupt_after`：节点**运行后**暂停——先看产出再决定是否继续

**方式二：`interrupt()` 函数（节点内动态中断）**

```python
from langgraph.types import interrupt, Command

def human_review_node(state: ApprovalState) -> dict:
    review_payload = {
        "question": "Approve this plan?",
        "plan": state["plan"],
    }
    # 调用 interrupt 会 checkpoint 状态并抛出异常，LangGraph 捕获它，执行在此暂停
    approval = interrupt(review_payload)
    # resume 之后，approval 里装的就是人工的决策
    return {"human_approved": approval.get("approved", False)}

# 恢复
final = app.invoke(
    Command(resume={"approved": True}),
    {"configurable": {"thread_id": "deploy-001"}},
)
```

**方式三：动态中断（条件触发）**——只有在风险逻辑命中时才暂停：

```python
def smart_executor(state):
    result = execute_action(state)
    if result.risk_score > 0.8:
        decision = interrupt({"reason": "High-risk action", "details": result})
        if not decision["proceed"]:
            return {"status": "cancelled"}
    return {"result": result}
```

> **硬性约束：Human-in-the-Loop 必须有 checkpointer。** 图状态必须被持久化，人工恢复时才能取回。**没有 checkpointer 时调用 `interrupt()` 会抛运行时错误。**

**生产级的恢复流程（FastAPI 示例）：**

```python
@app.post("/workflows/{thread_id}/approve")
async def approve_workflow(thread_id: str, decision: ApprovalDecision):
    config = {"configurable": {"thread_id": thread_id, "recursion_limit": 25}}
    result = graph.invoke(
        Command(resume={"approved": decision.approved, "comment": decision.comment}),
        config=config,
    )
    return {"status": "resumed", "output": result.get("final_answer", "")}

@app.get("/workflows/{thread_id}/status")
async def get_workflow_status(thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    state = graph.get_state(config)          # 不恢复，只看状态
    return {
        "status": "waiting_for_approval" if state.next else "complete",
        "next_node": list(state.next),
        "current_plan": state.values.get("plan", ""),
    }
```

**这段代码背后的核心洞察值得单独强调**：

> **在 `interrupt()` 与 `Command(resume=...)` 之间，跑这个图的 worker pod 是完全空闲的。** 状态存在 PostgreSQL 里，Kubernetes 部署里的任何 pod 都能接起这个 thread 继续跑。这就是人机协同工作流的**水平扩展能力**。

有一个真实的量级参考：某大型金融机构运行审批工作流，一个实例可以在中断状态停留 **72 小时**等合规官审核，而等待期间的**基础设施成本基本为零**——没有线程被阻塞，没有内存被占用。

### 4.3 Command：Handoff 与跨图通信

```python
from langgraph.types import Command

def agent_with_handoff(state: State) -> Command:
    """把任务移交给另一个 agent，并带上上下文。"""
    return Command(
        goto="specialist_agent",
        update={
            "context": state["analysis"],
            "handoff_reason": "needs expertise",
        },
    )
```

`Command` 一次调用同时做两件事：**路由**（goto）和**状态更新**（update）。`Command.PARENT` 可以跨子图边界向上路由。

### 4.4 Time Travel：调试神器

```python
# 取完整历史
history = list(app.get_state_history(config))

for snapshot in history:
    print(snapshot.values["messages"][-1].content[:80], snapshot.config)

# 从某个历史 checkpoint 分叉重放
target = history[2].config          # 往回三步

for event in app.stream(None, target, stream_mode="values"):
    pass                             # 从这里往前重放
```

**传 `None` 作为输入 = 从指定 checkpoint 重放；传具体值 = 先把值作为更新追加到那个 checkpoint 的状态再继续。**

这就是「不重跑整个对话就能复现一次不良 Agent 运行」的方法——把一个偶发的运行变成**确定性的状态机**。

### 4.5 多 Agent 拓扑

LangGraph 的可组合性是它的生产超能力：**每个子 Agent 本身就是一个完整编译好的 LangGraph 应用。** Supervisor 把它们当节点调用，它们内部的复杂度对外层图完全不可见。

#### 拓扑一：Supervisor / Orchestrator（最常用）

一个协调者 LLM 决定下一个调用哪个专家，每个专家报告回来。

```python
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages
from langchain_core.messages import AIMessage

class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]
    task: str
    next_agent: str

def supervisor_node(state: SupervisorState) -> dict:
    decision = llm.invoke(f"""You are a project manager. Task: {state['task']}
History: {state['messages']}
Which specialist acts next? Options: researcher | writer | reviewer | FINISH
Reply with only one word.""").content.strip()
    return {"next_agent": decision}

def route_to_agent(state: SupervisorState) -> str:
    return END if state["next_agent"] == "FINISH" else state["next_agent"]

# 每个专家本身可以是一个编译好的 LangGraph 应用
def researcher_node(state: SupervisorState) -> dict:
    result = research_graph.invoke(state)      # ← 子图！
    return {"messages": [AIMessage(content=f"Research: {result['analysis']}")]}

g = StateGraph(SupervisorState)
for name, fn in [("supervisor", supervisor_node),
                 ("researcher", researcher_node),
                 ("writer", writer_node),
                 ("reviewer", reviewer_node)]:
    g.add_node(name, fn)

g.set_entry_point("supervisor")
g.add_conditional_edges("supervisor", route_to_agent)

for agent in ["researcher", "writer", "reviewer"]:
    g.add_edge(agent, "supervisor")            # 永远回报给 supervisor

multi_agent_app = g.compile()
```

**官方提供了预置实现**，不用手写路由：

```bash
pip install langgraph-supervisor
```

```python
from langgraph_supervisor import create_supervisor
```

#### 拓扑二：Swarm（对等移交）

```bash
pip install langgraph-swarm
```

```python
from langgraph_swarm import create_swarm
```

与 Supervisor 的区别：没有中心协调者，Agent 之间**对等移交**（用 `Command(goto=...)`），适合流程更像「接力的传话」而非「上级派活」的场景。

#### 拓扑三：Hierarchical（层级式）

```
Supervisor
├── Research Team (subgraph)
│   ├── Searcher
│   └── Analyst
└── Writing Team (subgraph)
    ├── Drafter
    └── Editor
```

```python
research_team = create_research_team_graph()
writing_team = create_writing_team_graph()

main_graph = StateGraph(MainState)
main_graph.add_node("supervisor", main_supervisor)
main_graph.add_node("research_team", research_team.compile())
main_graph.add_node("writing_team", writing_team.compile())
main_graph.add_conditional_edges("supervisor", team_router)
main_graph.add_edge("research_team", "supervisor")
main_graph.add_edge("writing_team", "supervisor")
```

#### 拓扑四：并行 / Map-Reduce（Fan-Out / Fan-In）

先并行检索多个数据源再汇总——**顺序执行多个研究节点是企业 Agent 最常见的性能错误。**

```python
import operator
from typing import Annotated

class ResearchState(TypedDict):
    query: str
    web_results: Annotated[list[str], operator.add]     # ← 并行安全的 reducer
    db_results: Annotated[list[str], operator.add]
    doc_results: Annotated[list[str], operator.add]
    final_synthesis: str

async def web_search_node(state: ResearchState) -> dict:
    results = await web_search_tool.ainvoke(state["query"])
    return {"web_results": [results]}

async def database_query_node(state: ResearchState) -> dict:
    results = await db_tool.ainvoke(state["query"])
    return {"db_results": [results]}

async def synthesizer_node(state: ResearchState) -> dict:
    synthesis = await llm.ainvoke(
        f"Synthesize: Web={state['web_results']} DB={state['db_results']}"
    )
    return {"final_synthesis": synthesis.content}

research_graph = StateGraph(ResearchState)
research_graph.add_node("web_search", web_search_node)
research_graph.add_node("database_query", database_query_node)
research_graph.add_node("document_search", document_search_node)
research_graph.add_node("synthesizer", synthesizer_node)

# Fan-out：START 同时连向三个节点，它们并行执行
research_graph.add_edge(START, "web_search")
research_graph.add_edge(START, "database_query")
research_graph.add_edge(START, "document_search")

# Fan-in：三个都完成后才进 synthesizer
research_graph.add_edge("web_search", "synthesizer")
research_graph.add_edge("database_query", "synthesizer")
research_graph.add_edge("document_search", "synthesizer")
```

**关键点：并行分支写入的字段必须用并行安全的 reducer。** `operator.add` 就是标准做法（追加合并）。**如果并行分支写字段时用了默认 reducer（覆盖语义），就会出现前面说的静默 last-write-wins 数据丢失。**

### 4.6 Functional API（另一种风格）

除了 StateGraph，LangGraph 还提供 **Functional API**（基于 `tasks` 和 `entrypoints`），用更接近普通函数的写法获得同样的持久化和中断能力。适合逻辑是线性流程、但需要 checkpoint 的场景。StateGraph 更适合显式分支逻辑。

---

## 第 5 章 强相关技术：MCP、A2A、LangSmith

你说了「如有强相关技术也可以纳入」——这一章就是答案。**LangGraph 本身不解决「怎么连工具」和「怎么连别的 Agent」，这两个问题由协议层标准接管。** 而协议与框架的关系是「接口规范」与「实现引擎」的关系。

### 5.1 一句话讲清 MCP 与 A2A 的分工

> **MCP = 连接 Agent 到工具与数据（纵向）**
> **A2A = 连接 Agent 到 Agent（横向）**

**它们不是竞品，是同一栈的不同层。** 很多人被「MCP vs A2A」的文章标题误导，以为要二选一。实际上——一个现代生产级 Agent 会**同时用两个**。

最广为引用的两个比喻：

| 协议 | 比喻 | 含义 |
|---|---|---|
| **MCP** | AI 的 **USB-C** | 一个标准插口，任何工具服务器都能插上 |
| **A2A** | AI Agent 的 **HTTP** | 定义设备之间如何跨网络通信 |

### 5.2 MCP 详解

**出身**：Anthropic 于 2024 年 11 月发布，立即开源。
**解决的问题**：在 MCP 之前，每个 AI 框架都有自己的工具连接方式。**为 LangChain 写的 GitHub 集成，在 AutoGen 里不能用**；为 CrewAI 写的数据库连接器，换个框架就得重写。

**它消灭的是 N×M 问题**：5 个 Agent × 10 个工具 = 最坏情况 50 个定制集成；有了 MCP，写 10 个 server，所有讲 MCP 的 Agent 都能连。

**架构三部分**：

```
AI Agent (MCP Host)
    │
  MCP Client          ← host 内部管理连接、路由调用
    │  JSON-RPC 2.0
    │  stdio（本地）/ HTTP+SSE（远程，2025 年 3 月规范修订后为 Streamable HTTP）
    ↓
┌──────────┬──────────┬──────────┬──────────┐
│ GitHub   │ Postgres │ Salesforce│File System│
│ MCP Server│MCP Server│MCP Server │MCP Server │
└──────────┴──────────┴──────────┴──────────┘
```

**MCP Server 暴露三种资源类型**：

| 类型 | 含义 | 例子 |
|---|---|---|
| **Tools** | Agent 可调用的函数 | 查数据库、开 Jira ticket、发 Slack 消息 |
| **Resources** | Agent 可读取的数据/内容 | 文件、数据库记录、知识库 |
| **Prompts** | 预定义指令模板 | 不用从零构造的提示词模板 |

**LangChain 里的集成方式**（用 `langchain-mcp-adapters`）：

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

async with MultiServerMCPClient({
    "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
        "transport": "stdio",
    },
    "github": {
        "url": "https://your-github-mcp.example.com/sse",
        "transport": "sse",
    },
}) as client:
    tools = client.get_tools()      # 所有 MCP server 的工具现在都能在 LangChain 里用了
```

**生态规模（2026 年 5 月数据）**：月 SDK 下载量超 **9,700 万**，官方注册表 **9,600+ servers**，GitHub 上打了 `mcp-server` 标签的仓库近 **16,000** 个。OpenAI、Microsoft、AWS、Salesforce、ServiceNow 都通过 MCP 暴露工具。

**一个真实案例**：Block（Square）部署 MCP server 连接 Snowflake、GitHub、Jira、Slack、Google Drive 和内部 API，把 6 套独立的工具集成替换成了对全栈所有 Agent 单一的标准化接口层。

### 5.3 A2A 详解

**出身**：Google（+ 50 家合作公司）于 2025 年 4 月发布。
**解决的问题**：MCP 解决了工具连接，但 Agent 之间怎么通信？当 Agent A 想委派任务给 Agent B：A 怎么发现 B 能做什么？怎么发任务？如果任务要跑 30 分钟，怎么拿进度更新？如果 B 是另一家公司用另一个框架建的怎么办？

**四个核心原语**：

| 概念 | 作用 |
|---|---|
| **Agent Card** | JSON 清单，声明 Agent 能力（"I can do code review"）。发布在 `/.well-known/agent.json`（遵循 RFC 8615） |
| **Task** | 被委派的工作单元——创建、跟踪、异步完成 |
| **Artifacts** | 产出：文件、JSON、结构化结果，回传给 orchestrator |
| **SSE streaming** | 长任务实时回传进度 |

**任务生命周期**：`submitted → working → completed / failed`

**A2A 刻意对内部实现保持不透明**——它不关心远端 Agent 用什么框架、什么模型。这是有意设计，为的是实现**供应商中立的 Agent 协作**。

### 5.4 MCP 还是 A2A：实务判断表

这张表来自多个企业项目的验证，**是本次调研中价值最高的一块内容**，因为网上文章基本只讲概念不讲判断：

| 判断条件 | 用 MCP | 用 A2A |
|---|---|---|
| 对端是否具备自主决策能力 | 否（被动工具） | 是（自主 Agent） |
| 交互模式 | 同步请求-回应 | 异步、多轮对话 |
| 执行时间 | 毫秒至秒级 | 秒级至天级 |
| 输出可预测性 | 高（结构化回传） | 低（Agent 自主判断输出） |
| 失败处理 | 重试或回退 | 协商、重新指派、升级 |
| 典型案例 | 查资料库、调 API、读写文件 | 委派子任务、跨团队协作、汇总分析 |

**灰色地带怎么处理**：某些场景看似两条路都行。比如一个「文件摘要服务」——可以包成 MCP Server（工具化），也可以部署成独立的 A2A Agent。判断关键：

> 如果该服务**只需接收输入、回传输出，且不需要「思考」或「协商」**，则 MCP 更合适；
> 如果该服务**需要根据上下文自主决定摘要策略、可能要求补充资料、或会主动提出建议**，则 A2A 更合适。

**同时要预留演进路径**：一个原本作为 MCP Server 的服务，随着能力增强可能需要升级为 A2A Agent。架构设计时就该留好这个口子。

### 5.5 框架的协议支持现状

**协议是规范，框架是实现引擎。框架必须实现协议才能参与互通生态。**

| 框架 | MCP 支持 | A2A 支持 |
|---|---|---|
| **LangGraph / LangChain** | ✅ 原生 | ✅ 支持 |
| CrewAI | ✅ 原生 | 🔄 进行中 |
| AutoGen (Microsoft) | ✅ 支持 | ✅ 支持 |
| Google ADK | ✅ | ✅ 原生 |
| Mastra | ✅ 原生 | 🔄 进行中 |
| OpenAI Agents SDK | ✅ | 🔄 评估中 |
| PydanticAI | ✅ | 🔄 进行中 |

**Google ADK 明确演示了两者组合**：每个子 Agent 对外暴露 A2A 接口用于编排，内部用 MCP 工具。

**采用顺序的现实建议**：

> 大多数真实产品**先上 MCP**（工具连通性是刚需，收益立竿见影），**随着 Agent 数量增多再上 A2A**（协调成本才会成为瓶颈）。

### 5.6 2026 标准 Agent 技术栈

这是实践者总结的分层视图，可以直接当架构参考：

```
┌─────────────────────────────────────────────┐
│  框架层                                       │
│  LangGraph / CrewAI / AutoGen                │
├─────────────────────────────────────────────┤
│  工具连通性层                                 │
│  MCP（files / DBs / APIs / browser）          │
├─────────────────────────────────────────────┤
│  Agent 协调层                                 │
│  A2A（多 Agent 任务委派）                      │
├─────────────────────────────────────────────┤
│  可观测性层                                   │
│  LangSmith / Langfuse / Helicone / Phoenix   │
├─────────────────────────────────────────────┤
│  记忆持久化层                                 │
│  Mem0 / Zep / Letta                          │
└─────────────────────────────────────────────┘
```

**给新人的判断**：如果你只做一个单 Agent 接几个工具，**你只需要框架层 + MCP + 一个观测工具**。A2A 和记忆层是规模上来之后的事。**别一上来就把五层全铺开。**

### 5.7 其他协议与治理格局

| 协议 | 出处 | 现状 |
|---|---|---|
| **MCP** | Anthropic，2024-11 | 事实上标准，2026 年 5 月超 9,700 万月 SDK 下载 |
| **A2A** | Google，2025-04 | v1.0 支持 gRPC 与签名 Agent Cards |
| **ACP** | IBM | 已并入 A2A |
| **UTCP** | 社区 | 反主流方案——**无 wrapper 直连**，减少一跳，约 50% 延迟优势。适合对延迟极度敏感且工具本身就是 HTTP 服务的场景 |
| **LAP** | 社区 | 较少提及 |
| **LangChain Agent Protocol** | LangChain | 定义统一的 REST API 用于**部署和调用 Agent 作为服务**，引入 threads / runs / memory 概念。定位是「让 Agent 即插即用的运维包装层」 |

**治理格局**：MCP 与 A2A 现已由 Linux Foundation 下的 **Agentic AI Foundation（AAIF，2025 年 12 月成立）** 治理，146+ 成员组织。**两个协议都进了 Linux Foundation，都由主流厂商背书**——这个事实本身就说明了「不选边」的态度。

**三协议的分工（含 LangChain Agent Protocol）**：

| 协议 | 解决什么 | 最适合 | 类比 |
|---|---|---|---|
| **A2A** | Agent 与 Agent 对话 | 多 Agent 协作工作流 | Agent 的 Slack |
| **MCP** | Agent 访问工具与数据 | 企业集成 | 外部系统的 USB-C |
| **Agent Protocol** | Agent 作为服务被部署 | API 驱动的 Agent 访问 | Agent 的 API 规范 |

**一个完整的企业级例子**说明三者如何共存：

> 用户向分析 Agent 请求一份报告。
> 分析 Agent 用 **MCP** 拉取最新的 CRM 和财务数据。
> 它用 **A2A** 把可视化任务委派给图表生成 Agent。
> 每个 Agent 都通过 **Agent Protocol** 部署，所以 UI 能一致地调用它们。
> 结果流式返回给用户。

---

## 第 6 章 选型决策：什么时候用哪个

这是你最需要的一章。**「LangChain 和 LangGraph 该学哪个」这个问题的正确答案是：先学 LangChain，用到不够时再降级到 LangGraph。**

### 6.1 三层递进的官方推荐路径

官方明确的意图是：

> "In practice, most developers should start with `create_agent` and move to an explicit LangGraph graph or functional APIs when they need custom state transitions, durable checkpoints, complex routing, long-running loops, or fine-grained execution control."
>
> "This layered approach (high-level API first, lower-level orchestration when necessary) is **the intended path, not a workaround**."

（分层推进——先高层 API，必要时再下探到低层编排——**是官方设计意图，不是变通方案**。）

```
第 1 站：create_agent                    ← 从这开始
   ↓ 不够用了？
第 2 站：create_agent + Middleware        ← 先试这层能解决多少
   ↓ 还不够？
第 3 站：手写 StateGraph                  ← 明确需要状态机和分支时
   ↓ 还不够？
第 4 站：LangGraph + Functional API / 子图 / 多 Agent 拓扑
```

### 6.2 决策表：什么时候升级到 LangGraph

**用 `create_agent` 就够了（90% 的情况）**：

- 单 Agent 循环：模型 + 几个工具 + 一个 system prompt
- 横切关注点用 Middleware 能搞定（PII 脱敏、摘要压缩、人工审批、重试、护栏）
- 简单的人工审批——用 Middleware 或 interrupt 就能实现，**不必画图**

> 官方特别说明：**"Human approval, retries, guardrails, and some multi-agent handoffs can also be implemented through higher-level middleware and interrupts without defining a graph."** —— 这句话打破了很多人的误解，以为「要人工审批就必须上 LangGraph」。

**必须降到 LangGraph 的信号（满足任一）**：

| 信号 | 说明 |
|---|---|
| **分支控制流依赖中间 LLM 输出** | 典型是 router 模式，下一步去哪由一个模型的输出决定 |
| **多个专家角色需要协调但不该共享全部上下文** | 多 Agent 拓扑 |
| **人工暂停可能超过单个请求生命周期** | 审批要等几小时甚至几天 |
| **状态必须跨会话持久化，且需要重放或分支** | Time travel、审计回放 |
| **Agent 工具循环复杂到「while 循环里的 ReAct」已经不可读了** | 可维护性拐点 |
| **需要自定义状态转移、持久化 checkpoint、复杂路由、长时循环、细粒度执行控制** | 官方列举的这一串就是判断依据 |

### 6.3 反向决策：什么时候两者都不该用

**这一节的价值往往比上一节更高。** 以下情况用 LangChain/LangGraph 是**过度设计**：

| 场景 | 更该用什么 |
|---|---|
| 单次 prompt + 检索 + 结构化输出就能回答 | 直接调模型 SDK。**大多数 B2B AI 功能还在这里** |
| 固定三四步的线性流程，无分支 | 普通 Python 函数 / LangChain 简单管线就够 |
| 只需要单次对话内的短期记忆，且平台内置记忆够用 | 平台内置能力 |
| 产品问题还是「用户想不想要这个功能」，而非「怎么扩展编排」 | 别在没验证需求时引入编排复杂度 |

**LangGraph 的诚实代价**：Typed state、图接线、checkpointer 设置都成为**持续性的关注点**。回报是你能用可调试的运行时交付分支、多角色、持久化的 Agent 行为，而不是一堆条件判断和全局变量。

> **核心判断原则：当控制流本身就是你的问题时，才上 LangGraph。**

**另一条来自实践者的建议**（值得逐字读）：

> "Skip it when: It's a single agent calling one or two tools — the OpenAI Agents SDK or Pydantic AI is a lighter path. It's a deterministic workflow — plain code plus your llm helpers is simpler and cheaper to reason about. **You're brand new to agents — it has the steepest curve of the 2026 frameworks. Learn the pattern first, adopt the framework second.**"

（如果你是 Agent 新手——它是 2026 年各框架里学习曲线最陡的。**先学模式，再上框架。**）

这句话对「用 RAG 找 AI 应用开发工作」的你尤其重要：**面试官更想看你能不能讲清楚 Agent 循环的本质，而不是你背没背 LangGraph 的 API。**

### 6.4 与其他 2026 年主流框架的横向对比

| 框架 | 定位 | 何时选它 |
|---|---|---|
| **LangChain + LangGraph** | 最完整的分层栈 | 需要生产级持久化、人机协同、多 Agent；生态最大 |
| **OpenAI Agents SDK** | 轻量、handoff 与 guardrails 优先 | 纯 OpenAI 技术栈；单 Agent 或简单 handoff |
| **PydanticAI** | 类型安全、Pythonic | 团队重类型、要 Pydantic 一致性；单 Agent 为主 |
| **CrewAI** | 角色-任务抽象 | 团队更想要高层约定而非图的显式建模 |
| **AutoGen (AG2)** | 微软系，多 Agent 对话 | 已在微软生态；多 Agent 会话式协作 |
| **Google ADK** | A2A 原生 | Google Cloud 技术栈；跨组织 Agent 协作 |
| **Mastra** | TypeScript 优先 | Node 技术栈，想用图形态的 Agent 但不离开 JS 生态 |

**关于 TypeScript**：LangGraph.js 存在，但**生态较薄**——这是社区公认的现状，如果你的团队是 Node 栈，要提前评估这个差距。

---

## 第 7 章 生产实战：五个必然踩的坑与检查清单

这一章的内容来自多个生产事故复盘。**这些坑的共同特点是：本地开发和测试时完全看不出来，只在生产上、在上线几天或几周后才爆发。**

### 7.1 五个生产失败模式（按严重度排序）

#### 坑 1：Checkpoint 状态无界增长

**机制**：LangGraph 在每个 superstep 通过 checkpointer 持久化状态。对会话型 Agent，状态通常包含 `add_messages` reducer 累积的完整消息历史。

**为什么生产才爆**：消息列表只增不减，LangGraph 默认配置**不会**裁剪或摘要旧消息。每次 checkpoint 写入都会把完整累积状态（含全部消息历史）序列化到后端。

**症状链条**：
1. 消息数涨到几百条 → checkpoint 写入延迟成比例上升
2. 每轮发给 LLM 的 state payload 逼近或超出模型的上下文窗口
3. 悄无声息地截断旧上下文，或直接触发 context-length 错误（取决于模型客户端行为）
4. checkpointer 后端的存储按 thread 无界增长

**为什么测试发现不了**：用短对话做压测看不到——**这个失败只在消息数越过某个阈值后才显现**，而短测试对话永远达不到那个阈值。因此它是典型的「生产独有失败」，在真正长会话开启后几天或几周才浮现。

**修复**：
- 显式实现消息历史管理，**不要依赖无界累积**
- 用自定义 reducer 实现滑动窗口或摘要式裁剪，每轮执行或每 N 条消息触发一个摘要节点，把旧消息压缩成摘要消息，用「摘要 + 最近若干轮」替换原始历史
- **把「每个 thread 的 checkpoint payload 大小」当成运营指标监控**，超阈值告警——不要等到 context-length 错误或 checkpoint 写入超时才发现

```python
from langchain_core.messages import RemoveMessage

def trim_old_messages(state: ChargebackState) -> dict:
    old = state["messages"][:-10]                    # 保留最近 10 条
    return {"messages": [RemoveMessage(id=m.id) for m in old]}
```

#### 坑 2：递归 Agent 循环超出递归上限

**机制**：LangGraph 对图执行强制施加 `recursion_limit`（**默认 25 supersteps**），防止节点条件路由回自身或更早节点的失控循环——这正是 ReAct 风格 Agent 在「推理-调工具」之间交替直到决定结束的标准模式。

**为什么生产才爆**：当任务真的需要比默认值更多迭代时（比如一个研究 Agent 需要 30+ 次工具调用才能为复杂查询收集足够信息），图会在任务中途撞上递归上限。**开发期测试任务通常简单，远不到 25 次迭代就完成了**，所以这个上限在开发测试阶段是不可见的。

**症状**：抛出 `GraphRecursionError`，**整个运行被终止**，而不是返回 Agent 的部分进展或触发优雅降级。而且这是个 **Python 异常而非结构化 Agent 响应**——除非调用方显式捕获，否则它会作为未处理异常向上传播，**用户看到的是一个通用 500 错误，完全不知道 Agent 其实一直在取得进展**。

**修复（两个控制点，别混淆）**：

1. **基于经验设定 `recursion_limit`**：给图加埋点记录已完成运行的 superstep 数，用观察到的分布给最难的现实任务设一个有裕量的上限，**不要依赖默认值**。

2. **显式捕获 `GraphRecursionError`**：利用中断点的 checkpoint 状态，返回 Agent 的部分进展，或返回一条说明「任务需要的步骤超过允许值」的消息，并给用户继续的路径（从 checkpoint 以更高上限恢复，或者收窄请求）——而不是死胡同。

```python
try:
    result = app.invoke(input_data, config={"recursion_limit": 100})
except GraphRecursionError:
    state = app.get_state(config)
    return {"status": "error", "partial": state.values, "hint": "任务步骤超出上限"}
```

> **重要区分**：`recursion_limit` 报错说明**你在循环**，但**不告诉你为什么循环**。真正的修复是**确保你的 router 有一条通往 END 的路径**——这是两件不同的事，混淆它们是「下午两点生产事故」的常见成因。快速修复（调高上限）和根因修复（修 router）不能互相替代。

#### 坑 3：并发状态更新的静默数据丢失

**机制**：并行图分支同时更新同一状态字段时，**如果该字段的 reducer 没有为并发配置，会发生 last-write-wins 的静默数据丢失**。

**为什么危险**：它不报错。你只是发现某些并行的结果「不见了」。

**修复**：并行写入的字段用并行安全的 reducer（如 `Annotated[list, operator.add]`），并在设计阶段就明确每个字段的合并语义。

#### 坑 4：Checkpointer 后端连接耗尽

**机制**：在并发 Agent 会话下使用**内存或 SQLite checkpointer**，超出其设计规模后连接耗尽。

**症状**：并发上来后大量请求失败。

**修复**：生产用 `PostgresSaver` 并配好连接池（PgBouncer 事务模式是常见选择）。**别让「本地跑得好好的」的 SQLite 方案进生产。**

#### 坑 5：中断状态在部署重启后无法恢复

**机制**：**checkpoint 的 thread ID 映射没有持久化到进程内存之外**。部署重启后，Human-in-the-Loop 的中断状态变成不可恢复的孤儿。

**为什么严重**：用户已经审批完了，结果恢复不了；或者更糟——审批记录丢失但业务动作已执行。

**修复**：确保 thread_id 与业务实体的映射存在数据库里，而不是进程内存里。**这也从侧面说明了为什么中断流程必须用 Postgres 而非 MemorySaver。**

### 7.2 另外五个高频陷阱（工程实践类）

**陷阱 6：破坏性的 schema 变更**

你给 `AgentState` 加了个字段然后部署。**在途的旧 thread 反序列化时缺这个 key，下一个节点就崩。**

修复：
- 每个新字段都用 `NotRequired[...]` 加默认值，**或**
- 给状态做版本化并在加载时迁移，**或**
- 部署时让旧 checkpoint 失效（如果应用能容忍）
- **永远不要原地重命名字段**

**陷阱 7：checkpoint 表膨胀**

每个节点写入都创建一条新的 checkpoint 行。**一百万轮之后你有一张又大又慢的表。**

修复：跑 nightly job 删掉 N 天前的 thread checkpoint，或用 `PostgresSaver` 的 TTL 扩展。**别等数据库磁盘用到 90% 才发现这事。**

**陷阱 8：重试中的非幂等工具**

一个工具发了邮件。图的 checkpoint 紧接着失败了。**重放时，邮件又发一次。**

修复：让每个工具调用幂等——带一个确定性的请求键（比如 `f"{thread_id}:{step}"`），下游 API 用它去重。**这条对任何涉及钱、消息、外部副作用的工具都是硬要求。**

**陷阱 9：Router 幻觉**

条件边 router 输出了允许枚举之外的值。图路由到不存在的边而死锁，或掉到默认分支静默做错事。

修复：校验 router 输出；未知值路由到 recovery 节点重新追问；降低 router 模型 temperature；用结构化输出约束。

**陷阱 10：节点越权读取（Over-reading nodes）**

worker prompt 收到了完整 state，模型决定「友好一点」——回答超出自己职责范围的问题、质疑前序节点的结论、或者改写别的节点产出。

**症状**：多 Agent 图里下游节点「修正」上游工作，最终综合结果变得不连贯。

修复：**每个节点只传它需要的字段**（在节点里用小的 input-builder 函数裁剪）；在 prompt 里写明该节点的职责；显式禁止改写其他节点的字段；把做了两件事的节点拆开。

### 7.3 生产上线检查清单

这是把前面所有坑正向化的结果，**可以直接当 code review checklist 用**：

- [ ] **1. 用数字审计需求** —— 运行时、并发量、恢复要求、token 预算、负责人。**部署前先答清楚。**
- [ ] **2. 类型化的状态 schema** —— TypedDict 的每个字段都小而可序列化
- [ ] **3. PostgresSaver，不是 MemorySaver** —— 持久 checkpoint + 调优过的连接池
- [ ] **4. 用作用域隔离节点视野** —— `StateGraph(FullState, input=InputSchema, output=OutputSchema)`，**从开始就规划，后期补是重写**
- [ ] **5. FastAPI + gunicorn 容器** —— 多 worker、非 root、healthcheck、合理的超时
- [ ] **6. 共享状态的水平扩展** —— 按 in-flight runs 自动扩缩；**按 thread 加锁避免 checkpoint 竞争**
- [ ] **7. 每个节点都有 OpenTelemetry trace** —— 每个 span 带上 thread ID、step count、tokens。**四个 dashboard，不是四十个。**
- [ ] **8. 排除五个坑** —— 递归上限、checkpoint TTL、增量式 schema 变更、有界历史、幂等工具
- [ ] **9. 图版本化** —— 部署 `graph_v3` 时让 `v2` 并行运行，直到存量线程排空。**别直接原地替换。**
- [ ] **10. 从第一天埋好维度** —— session ID、user ID、prompt-version tag 在调用点就要有

### 7.4 部署前 60 秒设计法

在动笔画图之前，先做这个 60 秒设计（来自实践者的建议，极其实用）：

1. **命名节点。** 每个离散决策或副作用动作就是一个节点：「Agent 思考」「工具运行」「reviewer 批准」「响应流式输出」。**如果你列不出节点，说明这个任务还不是 Agent 形态的。**
2. **声明状态。** 最小化 TypedDict，每个 list 字段都要有 reducer。**别把所有东西塞进 messages**——把任务特定字段（工作计划、预算计数器、retrieved_docs）提到顶层。
3. **画边。** 除非下一步依赖模型输出，否则用静态边。**每个条件边都需要一个带具名分支的 router 函数。**
4. **提前选好 checkpointer。** 测试用 MemorySaver，其他一切用 Postgres/Redis/SQLite。**没 checkpointer 就没有恢复、没有中断、没有时间旅行。**
5. **中断点放在工具运行前，不是运行后。** 审批闸门应该设在通向副作用节点的**入边**上，这样你才能在损害发生前取消。

---

## 第 8 章 完整代码实战

### 8.1 示例一：客服工单分诊 Agent（推荐的入门实战）

这个例子的设计思路很值得学：**PII 在代码里剥离，模型只负责分类和查订单，高优先级由代码规则呼叫值班，全程按用户 checkpoint。** 一句话概括就是——**一端模糊（模型），两侧确定（代码）**。

```python
from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import InMemorySaver      # 生产换 PostgresSaver
from langchain_core.tools import tool
from langchain.chat_models import init_chat_model

model = init_chat_model("anthropic:claude-sonnet-4-5")

class State(TypedDict):
    messages: Annotated[list, add_messages]        # ← reducer 必须有
    urgency: str

@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order status by ID."""
    return {"id": order_id, "status": "delayed"}

tools = [lookup_order]
agent_model = model.bind_tools(tools)

def strip_pii(state: State) -> dict:
    """确定性的脱敏，在模型看到任何东西之前。"""
    return {"messages": [redact(m) for m in state["messages"]]}

def agent(state: State) -> dict:
    return {"messages": [agent_model.invoke(state["messages"])]}

def route_urgency(state: State) -> str:
    return "page" if state.get("urgency") == "high" else "done"

def page_oncall(state: State) -> dict:
    notify_oncall()          # 代码拥有副作用
    return {}

b = StateGraph(State)
b.add_node("strip_pii", strip_pii)
b.add_node("agent", agent)
b.add_node("tools", ToolNode(tools))
b.add_node("page", page_oncall)

b.add_edge(START, "strip_pii")
b.add_edge("strip_pii", "agent")
b.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: "route"})
b.add_edge("tools", "agent")
b.add_conditional_edges("route", route_urgency, {"page": "page", "done": END})
b.add_edge("page", END)

app = b.compile(checkpointer=InMemorySaver())
```

**这个例子的三个可讲解的设计点**（面试时是加分项）：

1. **`strip_pii` 是独立节点而非 Middleware** —— 因为它是确定性的、必须在模型之前执行的硬约束。用节点表达比用隐式包装更清晰、更可测。
2. **`route_urgency` 用代码规则而非让模型判断** —— 高优先级是否呼叫值班是业务规则，不该交给概率模型。
3. **副作用收敛在 `page_oncall` 一个节点** —— 便于加审批闸门（在它的入边设 interrupt），便于审计。

### 8.2 示例二：带审批闸门的研究 Agent

```python
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.types import interrupt, Command
from langgraph.checkpoint.postgres import PostgresSaver

class ApprovalState(TypedDict):
    messages: Annotated[list, add_messages]
    plan: str
    human_approved: bool
    final_result: str

def planner_node(state: ApprovalState) -> dict:
    plan = llm.invoke(f"制定一个计划来完成：{state['messages'][-1].content}").content
    return {"plan": plan}

def human_review_node(state: ApprovalState) -> dict:
    """暂停执行，等待人工审批。"""
    human_input = interrupt({
        "question": "Approve this plan?",
        "plan": state["plan"],
    })
    return {"human_approved": human_input.get("approved", False)}

def executor_node(state: ApprovalState) -> dict:
    if not state["human_approved"]:
        return {"final_result": "Task cancelled."}
    result = llm.invoke(f"Execute this plan:\n{state['plan']}").content
    return {"final_result": result}

# Checkpointer 是必需的 —— 它在 interrupt 和 resume 之间存储状态
with PostgresSaver.from_conn_string("postgresql://...") as checkpointer:
    checkpointer.setup()

    workflow = StateGraph(ApprovalState)
    workflow.add_node("planner", planner_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("executor", executor_node)

    workflow.set_entry_point("planner")
    workflow.add_edge("planner", "human_review")
    workflow.add_edge("human_review", "executor")
    workflow.add_edge("executor", END)

    app = workflow.compile(checkpointer=checkpointer)

    # ── 第 1 步：跑到中断点 ──────────────────────────────
    thread = {"configurable": {"thread_id": "deploy-001"}}
    state = app.invoke(
        {"messages": [{"role": "user", "content": "部署支付服务到生产环境"}]},
        thread,
    )
    print("Plan:", state["plan"])
    print("— WAITING FOR APPROVAL —")

    # ── 第 2 步：人工审核后恢复 ──────────────────────────
    final = app.invoke(Command(resume={"approved": True}), thread)
```

### 8.3 示例三：把 LangGraph 部署成纯 FastAPI 服务

**这是最实用、最少人讲的一条路径。** 不需要 LangGraph API server，不需要 license key：

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from langgraph.checkpoint.postgres import PostgresSaver

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时建立连接池和 checkpointer
    async with PostgresSaver.from_conn_string(DB_URL) as cp:
        await cp.setup()
        app.state.graph = build_graph().compile(checkpointer=cp)
        yield

api = FastAPI(lifespan=lifespan)

@api.post("/chat/{thread_id}")
async def chat(thread_id: str, body: ChatRequest):
    config = {
        "configurable": {
            "thread_id": f"{body.user_id}:{thread_id}",   # ← 复合 thread_id 防冲突
            "recursion_limit": 15,                        # ← 保守设值
        }
    }
    try:
        result = await api.state.graph.ainvoke(
            {"messages": [{"role": "user", "content": body.message}]},
            config=config,
        )
        return {"reply": result["messages"][-1].content}
    except GraphRecursionError:
        state = await api.state.graph.aget_state(config)
        return {"status": "needs_more_steps", "partial": state.values}
```

**这个示例体现了前面所有生产建议**：Postgres checkpointer、复合 thread_id、保守 recursion_limit、递归异常的优雅降级。

---

## 第 9 章 面试考点与答法

### 9.1 高频概念题

**Q1：LangChain 和 LangGraph 是什么关系？**

> **答**：不是竞品，是同一技术栈的上下两层。LangChain 是高层 Agent 构建框架，提供 `create_agent`、Middleware、provider 无关的模型抽象；LangGraph 是低层编排运行时，提供持久化执行、人机协同、流式输出和状态管理。**关键事实是 LangChain 1.0 的 `create_agent` 底层就跑在 LangGraph 运行时之上。** 官方对此的定位是「LangChain 是 Agent 框架，LangGraph 是编排运行时」，同时也说明「用 LangChain 不需要懂 LangGraph」。实践中从 `create_agent` 起手，需要自定义状态转移、持久化 checkpoint、复杂路由或细粒度执行控制时再下探到 LangGraph。

**加分点**：提到 2025 年 10 月 22 日的 1.0 双发布，以及这是在 2.0 之前无破坏性变更的承诺节点。

**Q2：什么是 LangChain 的 Middleware？举几个实际用途。**

> **答**：Middleware 是 `create_agent` 的定义性特性，它让你在 Agent 循环的六个钩子点介入定制，而不用继承子类。六个钩子是 `before_agent`、`before_model`、`wrap_model_call`、`wrap_tool_call`、`after_model`、`after_agent`。实际用途包括：在 `wrap_tool_call` 里做工具调用的鉴权和审计；在 `before_model` 里做 PII 脱敏和动态上下文注入；在 `after_model` 里做输出校验；在 `after_agent` 里写回长期记忆。官方预置了 Human-in-the-loop 审批、摘要压缩、PII 脱敏和工具重试四个中间件。

**加分点**：说明「Middleware 把横切关注点从 Agent 主体里抽出来，变成可复用的插件」，以及这解决了老版本「抽象太重、定制要重写」的反馈。

**Q3：LangGraph 的 State 为什么要用 `Annotated[list, add_messages]`？**

> **答**：这是 reducer 语法，规定了「节点返回该字段更新时如何合并」。`add_messages` 的语义是追加并按 message id 去重；如果写成裸 `list`，每个节点返回的 messages 会**整体覆盖**历史，症状是 Agent 运行中途「失忆」——只记得最后一个节点给的消息。**这是 LangGraph 最常见的 bug。** 更严重的是并行分支场景：如果并行分支更新同一字段而 reducer 没为并发配置，会发生 last-write-wins 的静默数据丢失，且不报错。

**Q4：checkpointer 有哪几种？生产选哪个？**

> **答**：四种。`MemorySaver` 仅测试用，重启即丢；`SqliteSaver` 单机持久，不适合多 worker；`RedisSaver` 低延迟，适合已有 Redis 基建；**生产默认 `PostgresSaver`**，多 worker 共享状态、支持持久 checkpoint 和并发连接池。官方原话是「MemorySaver is for tests. Anything that persists across restarts wants a real store.」

**加分点**：补充「选择错误的 checkpointer 是最常见的 LangGraph 生产错误」，以及 Postgres 需要配 PgBouncer 事务模式连接池。

**Q5：LangGraph 怎么做 Human-in-the-Loop？**

> **答**：三种方式。① `interrupt_before` / `interrupt_after` 在 compile 时声明，在指定节点前后暂停；② 在节点内调 `interrupt()` 做动态中断，恢复时用 `Command(resume=...)` 注入人工决策；③ 条件触发，只在风险逻辑命中时暂停。**硬性约束是必须有 checkpointer**，否则 `interrupt()` 会抛运行时错误。

**加分点（这段最能体现深度）**：在 `interrupt()` 和 `Command(resume=...)` 之间，**跑图的 worker pod 完全空闲**，状态存在 Postgres 里，K8s 里任何 pod 都能接起这个 thread 继续。所以人机协同审批可以水平扩展，中断状态能停留几天而基础设施成本近乎为零。

**Q6：MCP 和 A2A 有什么区别？该用哪个？**

> **答**：一句话——**MCP 连接 Agent 到工具和数据（纵向），A2A 连接 Agent 到 Agent（横向）**，它们不是竞品，解决同一栈不同层的问题。MCP 由 Anthropic 2024 年 11 月发布，用 JSON-RPC 2.0，暴露 Tools/Resources/Prompts 三类资源，消灭的是「5 个 Agent × 10 个工具 = 50 个定制集成」的 N×M 问题。A2A 由 Google 2025 年 4 月发布，用 Agent Card 做能力发现、Task 做异步委派、Artifacts 回传结果。判断依据：对端是**被动工具**、同步请求-回应、毫秒到秒级、输出可预测 → 用 MCP；对端是**自主 Agent**、异步多轮、秒级到天级、输出依赖自主判断 → 用 A2A。**两者都由 Linux Foundation 下的 Agentic AI Foundation 治理**，真实生产系统通常同时用。

**Q7：LangGraph 的递归上限默认多少？撞上会发生什么？**

> **答**：默认 **25 个 supersteps**。撞上抛 `GraphRecursionError`，**整个运行终止**，不返回部分进展。这是防止 ReAct 风格 Agent 失控循环的保护机制。问题是**它是个 Python 异常而非结构化响应**，除非显式捕获，否则用户看到通用 500 错误。修复分两层：基于埋点数据为最难任务设一个有裕量的 `recursion_limit`；同时显式捕获异常，用中断点的 checkpoint 返回部分进展并给用户继续路径。

**加分点**：「`recursion_limit` 报错告诉你**在循环**，但不告诉你**为什么循环**。真正的修复是确保 router 有一条通往 END 的路径——这叫根因修复，调高上限只是快速止血，两者不能互相替代。」

### 9.2 设计与权衡题

**Q8：什么时候你会从 `create_agent` 升级到手写 StateGraph？**

> **答**：满足任一时升级——分支控制流依赖中间 LLM 输出（router 模式）；多个专家角色需协调但不该共享全部上下文；人工暂停可能超过单个请求生命周期；状态需跨会话持久化且要重放或分支；Agent 工具循环复杂到「while 循环里的 ReAct」已经不可读；需要自定义状态转移或细粒度执行控制。
>
> **反过来，什么不需要升级**：单 Agent 循环；横切关注点用 Middleware 能搞定的；**甚至简单的人工审批和部分多 Agent handoff 也能用 Middleware 和 interrupt 在高层次实现，不必画图**——这一点很多人误解。

**Q9：什么时候你会说「这个项目不该用 LangChain/LangGraph」？**

> **答**：单次 prompt + 检索 + 结构化输出就能回答的（大多数 B2B AI 功能还在这里）；固定三四步无分支的线性流程；只需要单次会话内短期记忆且平台内置够用；产品问题还是「用户想不想要」而非「怎么扩展编排」。
>
> **诚实地说，LangGraph 的代价是**：typed state、图接线、checkpointer 设置都成为持续性关注点；学习曲线是 2026 年各框架里最陡的；持久化带来数据库运维依赖；TypeScript 版生态较薄。**核心判断原则是：当控制流本身就是你的问题时，才上 LangGraph。**

**Q10：LangGraph 生产上最容易出什么问题？**

> **答**：五个高频失败模式。① **checkpoint 状态无界增长**——消息历史只增不减，checkpoint 写入延迟按比例上升，最终撞上下文窗口；② **递归超限**——默认 25，复杂任务撞上限，抛裸异常；③ **并发状态更新静默丢失**——并行分支写同一字段而 reducer 没配并发语义；④ **checkpointer 连接耗尽**——并发会话下用了内存或 SQLite；⑤ **中断状态部署重启后不可恢复**——thread ID 映射没持久化到进程内存之外。
>
> **共同特点是本地开发和短对话测试完全看不到**，只在生产上、上线几天后才爆发。

### 9.3 减分项清单

面试中**避免**说这些：

| 减分说法 | 问题所在 | 该怎么改 |
|---|---|---|
| 「LangChain 和 LangGraph 二选一」 | 暴露认知停留在 2024 年 | 说明是同一栈的上下两层 |
| 「用 `create_react_agent` 建 Agent」 | 已弃用 | 说 `langchain.agents.create_agent` |
| 「LangChain 就是包装 API 的」 | 忽视了 Middleware 和标准内容块 | 讲三个实际价值 |
| 「要人工审批就必须上 LangGraph」 | 过于绝对 | Middleware 和 interrupt 也能在高层次实现 |
| 「用 `MemorySaver` 就行」 | 生产反模式 | 明确指出生产用 Postgres |
| 「`messages: list` 就够了」 | 不知道 reducer | 讲清 `add_messages` 与覆盖陷阱 |
| 「MCP 和 A2A 我选 MCP」 | 把它们当竞品 | 说明是纵向/横向互补 |

### 9.4 项目经历话术（STAR 模板）

用 LangGraph 做过的项目，按这个结构讲：

> **S（背景）**：我们的客服系统原来的做法是单个 prompt + 检索，遇到需要查订单、判断紧急度、可能要转人工的工单就处理不了，而且没有长会话记忆。
>
> **T（任务）**：需要一个能多轮工具调用、能按用户隔离会话、在高风险动作前有人工闸门的 Agent，同时要能追踪每一步到底做了什么。
>
> **A（行动）**：
> - 用 `create_agent` 做主体，先用 Middleware 解决 PII 脱敏和长历史摘要两个横切问题，**没有一上来就画图**
> - 后来发现紧急度路由和人工审批闸门需要确定性控制流，**才降级到手写 StateGraph**，把 `strip_pii` 和 `page_oncall` 做成独立节点
> - checkpointer 用 `PostgresSaver` 配 PgBouncer，thread_id 用 `{user_id}:{session_id}` 复合形式
> - 上线前做了一轮失败模式排查：加了消息裁剪节点、把 `recursion_limit` 从默认 25 调到 15 并显式捕获 `GraphRecursionError` 返回部分进展、所有涉及外发消息的工具都加了 `{thread_id}:{step}` 幂等键
> - 用 LangSmith 全链路追踪，每个 span 带 thread ID 和 token 数
>
> **R（结果）**：工单一次解决率提升 X%，需要人工介入的场景从「全部人工」降到「只有高风险动作」；同时因为 checkpoint 和 trace 都在，出问题能直接时间旅行回放定位，而不是靠猜。

**这个话术的杀伤力在于最后那段排查**——它证明的不是「我会用这个框架」，而是「我理解它会怎么坏」。

---

## 第 10 章 学习路线与误区清单

### 10.1 三周学习路线

**第 1 周：LangChain 层（建立正确认知 + 能写代码）**

| 天 | 任务 |
|---|---|
| 1 | 读官方 1.0 发布博客，**先把「竞品」认知彻底清掉**；装 LangChain 1.x，跑通 `create_agent` 八行示例 |
| 2 | 吃透工具定义：`@tool` 装饰器、docstring 为什么重要、`bind_tools` 机制、工具返回值的处理 |
| 3 | Middleware 六钩子逐个过一遍；用装饰器写一个 `before_model`，用类写一个 `wrap_tool_call` |
| 4 | 试官方四个预置 Middleware（PII / Summarization / HITL / ToolRetry），理解各自介入点 |
| 5 | 结构化输出（Pydantic schema + `response_format`）；研究 `content_blocks` 结构 |
| 6-7 | **小项目**：做一个能查文档、能算数、能拒答越界问题的问答 Agent。**刻意用 Middleware 而不是 if-else 实现护栏** |

**第 2 周：LangGraph 层（掌握运行时）**

| 天 | 任务 |
|---|---|
| 8 | StateGraph 三原语；**手写一遍 ReAct Agent**（不抄，从空白开始写） |
| 9 | **Reducer 专题：刻意制造一次「messages 写成裸 list 导致失忆」的 bug 并修好。** 这个体感比读十篇文章都值 |
| 10 | Checkpointer 四后端对比；给第 8 天的 graph 接上 Postgres；写一个两轮对话验证记忆生效 |
| 11 | `stream_mode` 的 `values` vs `updates`；搭一个最小前端把 thinking 过程显示出来 |
| 12 | Human-in-the-Loop 三种方式全练一遍，重点理解 `Command(resume=...)` 和 `update_state` |
| 13 | Time travel：`get_state_history` + 从历史 checkpoint 分叉重放 |
| 14 | **小项目**：把第 1 周的项目改成 StateGraph，加上人工审批闸门 |

**第 3 周：生态与生产化（补齐短板，准备面试）**

| 天 | 任务 |
|---|---|
| 15 | 多 Agent 三种拓扑（Supervisor / Swarm / Hierarchical），用 `langgraph-supervisor` 跑一遍 |
| 16 | 并行 Fan-Out/Fan-In；**刻意练习并行分支的 reducer 配置** |
| 17 | 接一个 MCP server（建议从 filesystem 或 github 开始），理解 tools/resources/prompts 三类资源 |
| 18 | 读 A2A 的 Agent Card 规范；理解为什么真实系统两个都要 |
| 19 | LangSmith 接入并读懂一个完整 trace；对比试一次 Langfuse，体会差异 |
| 20 | **逐条过第 7 章的生产检查清单**，给项目补齐缺项 |
| 21 | 整理项目话术；把第 9 章的考点自测一遍 |

### 10.2 常见误区清单（收藏级）

**认知类**

1. ❌ 「LangChain 和 LangGraph 是竞品，要选一个」
   ✅ 同一栈的上下两层，`create_agent` 跑在 LangGraph 运行时上

2. ❌ 「学 LangGraph 必须先精通 LangChain」
   ✅ 官方明确说「用 LangChain 不需要懂 LangGraph」，反向也不强制

3. ❌ 「LangChain 只是个 API wrapper，学不到东西」
   ✅ Middleware 的横切可组合设计、Standard content blocks 的 provider 抽象，都是值得学的工程设计

4. ❌ 「网上说 `create_react_agent` 是最佳实践」
   ✅ 已弃用，用 `langchain.agents.create_agent`

**技术类**

5. ❌ `messages: list`
   ✅ `messages: Annotated[list, add_messages]`

6. ❌ 生产用 `MemorySaver`
   ✅ 生产用 `PostgresSaver`

7. ❌ 只用 `thread_id="user_123"`
   ✅ 用 `"{user_id}:{session_id}"`，避免多会话串台

8. ❌ 依赖 `recursion_limit` 默认值 25
   ✅ 基于埋点设值，并显式捕获 `GraphRecursionError`

9. ❌ 让消息历史无限累积
   ✅ 加裁剪节点或摘要节点

10. ❌ 并行分支写同一字段却用默认 reducer
    ✅ 用 `Annotated[list, operator.add]` 等并发安全 reducer

11. ❌ 工具里的副作用不做幂等
    ✅ 用 `f"{thread_id}:{step}"` 作为确定性请求键

12. ❌ 原地重命名 state 字段
    ✅ 只做增量变更，新字段用 `NotRequired[...]` 加默认值

**架构类**

13. ❌ 一上来就做 Agentic / 多 Agent 架构
    ✅ 从 `create_agent` 单 Agent 开始，控制流成为瓶颈时才上 LangGraph

14. ❌ 以为「要人工审批就必须上 LangGraph」
    ✅ Middleware 和 interrupt 在高层次也能实现

15. ❌ 把 MCP 和 A2A 当竞品
    ✅ 纵向 vs 横向，互补

16. ❌ 一开始就把「框架 + MCP + A2A + 观测 + 记忆」五层全铺开
    ✅ 单 Agent + MCP + 一个观测工具就够起步

17. ❌ 所有逻辑都塞进一个大节点
    ✅ 每个离散决策或副作用动作一个节点，并做作用域隔离

**工程类**

18. ❌ 上线前不做失败模式排查
    ✅ 逐条过生产检查清单

19. ❌ 不埋 trace 就开始调 Agent 行为
    ✅ 从第一天接观测工具，并且埋好 session/user/prompt-version 维度

20. ❌ 直接原地替换 graph 版本
    ✅ 并行运行 `graph_v3` 与 `v2`，直到存量线程排空

### 10.3 一句话总纲

如果三周之后你只记住一句话，记住这个：

> **LangChain 是「怎么快速搭出一个 Agent」，LangGraph 是「怎么让这个 Agent 在生产上活下来」。**
>
> **前者是起手式，后者是保命符。先用前者跑通，再用后者兜底，什么时候上后者取决于你的控制流有多复杂——而不是取决于哪个词在网上的热度更高。**

---

## 附录：术语表

| 术语 | 含义 |
|---|---|
| **`create_agent`** | LangChain 1.0 的标准 Agent 构建入口，取代 `AgentExecutor` 与 `create_react_agent` |
| **Middleware** | `create_agent` 的定制机制，通过六个钩子介入 Agent 循环 |
| **Standard content blocks** | `content_blocks` 属性，provider 无关地暴露推理链、引用、工具调用 |
| **`langchain-classic`** | 承接遗留功能（老 chains、retrievers、indexing API、hub、community 导出）的独立包 |
| **StateGraph** | LangGraph 的核心 API，用于构建状态机式工作流 |
| **Reducer** | 定义状态字段如何合并的函数，如 `add_messages`（追加去重）、`operator.add`（追加） |
| **Node** | 图的工作单元，纯函数：state → state 更新（delta） |
| **Edge** | 决定执行流的连接，分静态边、条件边、扇出/扇入 |
| **Router** | 条件边使用的函数，返回决定下一步去哪个节点的值 |
| **Checkpointer** | 持久化图状态的组件，MemorySaver / SqliteSaver / RedisSaver / PostgresSaver |
| **Thread** | 一次会话的状态隔离单元，由 `thread_id` 标识 |
| **Superstep** | 图的执行步进单位，`recursion_limit` 计数以此为单位（默认 25） |
| **`interrupt()`** | 节点内动态中断函数，需 checkpointer 支持 |
| **`Command`** | 一次调用同时完成路由（goto）与状态更新（update）；`Command(resume=...)` 用于恢复中断 |
| **Subgraph** | 作为节点被调用的完整图，用于层级式多 Agent |
| **Supervisor** | 中心协调者拓扑，一个 LLM 决定调用哪个专家 |
| **Swarm** | 对等移交拓扑，Agent 之间直接 handoff，无中心协调者 |
| **Fan-Out / Fan-In** | 并行执行多分支后汇总的 Map-Reduce 模式 |
| **Time travel** | 通过 `get_state_history` 与 checkpoint 重放历史状态 |
| **Functional API** | LangGraph 的另一种写法，基于 `tasks` 和 `entrypoints` |
| **MCP** | Model Context Protocol，Anthropic 2024-11 发布，连 Agent 到工具与数据 |
| **A2A** | Agent2Agent Protocol，Google 2025-04 发布，连 Agent 到 Agent |
| **Agent Card** | A2A 中的 JSON 能力清单，发布在 `/.well-known/agent.json` |
| **Agentic AI Foundation (AAIF)** | Linux Foundation 下治理 MCP 与 A2A 的组织，2025-12 成立 |
| **LangSmith** | LangChain 官方可观测性/评估/Prompt 管理平台（闭源 SaaS） |
| **LangSmith Deployment / LangGraph Platform** | 同一托管部署产品的双名，托管 Agent 运行的基础设施 |
| **LangServe** | 把 LangChain Runnable 暴露为 HTTP API 的旧方案，新项目建议改用 LangGraph Platform |
| **OpenInference** | Arize 主导的 OpenTelemetry 语义约定规范，用于 LLM 应用埋点 |

---

## 参考来源

本手册的技术事实主要来自以下渠道的交叉验证（2026 年 9 月检索）：

- LangChain 官方博客《LangChain and LangGraph Agent Frameworks Reach v1.0 Milestones》
- LangChain Changelog（1.0 于 2025-10-22、LangGraph 1.0 于 2025-10-23、LangChain 1.1 于 2025-12-02）
- LangChain 官方文档 docs.langchain.com（v1 迁移指南、中间件文档、LangGraph 概念文档）
- LangGraph 官方文档与中文站 langgraph.com.cn
- PyPI 版本信息（langchain-core 1.5.3、langgraph 1.2.11，2026-05-04）
- 多篇生产实践复盘：《LangGraph in production: five failure patterns》《Deploy LangGraph to Production: A Step-by-Step Tutorial (2026)》《LangGraph State Machines for Production》
- 协议对比资料：《MCP vs A2A: The Two Protocols Every AI Agent Developer Needs to Understand (2026)》《MCP vs A2A Protocol: Which AI Agent Standard Does Your Stack Need?》《MCP vs A2A vs LangChain Agent Protocol》
- 生态选型资料：《Is LangChain Worth It in 2026?》《LangSmith Alternatives (2026)》《Best LLM Observability Platform Guide 2026》

**关于时效性的说明**：LangChain / LangGraph 生态演进很快（1.0 之后数月内已到 1.2.x）。本手册的技术判断以 2026 年 9 月为准。若你在更晚的时间阅读，建议优先核实三件事：① `create_agent` 的 API 是否有变更；② LangGraph Platform 相关产品的命名与定价；③ MCP / A2A 的规范版本与治理状态。

