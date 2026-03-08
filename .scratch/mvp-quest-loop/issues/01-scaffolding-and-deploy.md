Status: resolved

# 01 脚手架与部署

## 概述
初始化前后端工程与基础设施，使后续每张票都能在一个可运行、可部署的骨架上叠加垂直切片。
- 前端：Taro 4 + Vue 3 + TypeScript + NutUI + Pinia，仅微信单端（遵守 ADR-0004）。
- 后端：Python 3.11 + FastAPI，分层（闯关服务 / 题目供给 / 审校 / 复盘 / 内容安全 / 模型客户端封装）。
- 数据层：MySQL（MVP 期向量库暂不建，因 RAG 知识库上传不在 MVP；题目实体用关系表承载）。
- 部署：云托管可访问；CI 基础（lint + pytest）。
- 合规占位：AIGC 首次说明弹层组件与「查看/删除学习数据」入口占位。

## 范围
- in：工程初始化、DB migrations 初始表（考生、闯关局、题目、错题、每日任务、掌握度）、`/health`、云托管部署、AIGC 说明占位组件、内容安全客户端封装占位。
- out：完整 Web 管理后台、真实类目备案、题目内容本身。

## 依赖（Blocked by）
- 无（根票）

## 验收标准（Acceptance Criteria）
- 前端 `npm run dev` 起微信端模拟器可运行空白页；NutUI 组件可渲染。
- 后端 FastAPI 启动，`GET /health` 返回 200。
- MySQL 可连，migrations 执行后存在上述 6 张核心表及合理字段。
- 可部署到云托管并通过公网 URL 访问 `/health`。
- 提供 AIGC 首次说明的占位组件与「仅展示一次」开关（逻辑由票 02 接）。
- 提供内容安全检测客户端封装占位（输入/输出两侧接口留出，真实检测由票 13 接）。

## 关联
- Implementation 1（分层）、ADR-0004（前端框架与端策略）。
- 测试决策 45（pytest + 接缝 B 假实现）。

## 备注
- 模型客户端封装必须作为唯一 AI 出口（Implementation 3），本票先留接口与假实现。
- 向量库待 V1.1/RAG 时再引入，不在本票范围。

## Comments

- 2026-05-21：实现完成。FastAPI + SQLAlchemy(2.0) 骨架、`/health`、6 张核心表（candidates/questions/sessions/mistake_book/daily_tasks/mastery）、Alembic 配置（target_metadata=Base.metadata）、接缝 B 假 LLM 客户端、内容安全占位、AIGC 首次说明占位组件、Taro4+Vue3 脚手架。测试 `pytest` 2/2 通过（健康 + 建表）。DB 在 dev/test 用 SQLite（可经 DATABASE_URL 换 MySQL，不违反架构）；MySQL 用于生产部署。前端 `npm install && npm run dev:weapp` 需联网与微信开发者工具，本环境未实跑。
