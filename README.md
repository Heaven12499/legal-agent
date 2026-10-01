# 合同审查助手--证据约束型法律 Agentic RAG 系统

面向合同法务初审场景：上传合同或直接提问，系统定位待核查条款，通过混合检索找到相关法律原文，并输出带证据引用的风险说明。

项目重点不是让模型“生成一段法律回答”，而是把合同条款稳定映射到可核验的法律依据，并对最终引用进行真实性和本轮证据校验。输出仅供人工初审参考，不替代律师判断。

> 核心链路：问题路由 → Query Rewrite → Vector + BM25 + RRF → 相邻法条扩展 → Evidence Whitelist → Citation Verification → Limited Reflection

---

## 核心能力

- **合同条款核查**：识别违约金、格式条款、竞业限制等重点条款，逐项给出风险说明和法律依据。
- **混合检索**：结合 bge-small-zh 向量召回与 BM25 词面匹配，通过 RRF 融合结果；可选 Cross-Encoder reranker。
- **可追溯引用**：每处法律引用必须真实存在于本地语料，且来自本轮检索证据；异常引用会被纠错或明确标警告。
- **Agent 工具调用**：单智能体自主决定检索、精确查询法条或直接回答，设置最大轮次防止无限循环，并记录完整 trace。
- **完整 Web 体验**：支持 docx、PDF、TXT 上传，多轮会话、消息修改与重新生成、PostgreSQL 持久化，以及 JWT 多用户隔离。
- **可靠长任务**：FastAPI 通过 Transactional Outbox 向 RabbitMQ 发布任务，独立 Celery Worker 以租约和幂等键消费；支持进度查询、自动/人工重试及故障接管。
- **生产可观测性**：JSON 结构化日志、请求/任务链路 ID、健康与就绪检查、Prometheus 指标和 Redis 分布式限流。
- **可复现评测**：合成合同用于回归测试，公开合同短条款用于外部验证，两类结果分开报告。

## 系统流程

~~~text
Vue 3 → FastAPI ──→ PostgreSQL + pgvector（会话、任务、法律版本、向量、Outbox、Trace）
             │
             ├──→ Redis（用户/IP 限流）
             └──→ RabbitMQ → Celery Worker → Agentic RAG → PostgreSQL
~~~

~~~text
合同条款 / 用户问题
  → Retrieval Routing
      ├─ 非法律信息问题 → 直接回答
      └─ 法律问题
          → Query Rewrite
          → Hybrid Retrieval（Vector + BM25 + RRF）
          → 相邻法条扩展
          → Evidence Whitelist
          → Citation Verification
          → Limited Reflection
          → 带证据的人工参考结论
~~~

检索与生成过程会持久化查询改写、工具调用、命中文档和引用校验结果，刷新或重启后仍可在前端追踪答案依据。

## 评测结果

### 公开合同短条款集

公开数据集包含 25 份政府采购公开合同、70 条二次脱敏短条款。对旧标签审计后，修正了一条被误标为负例的付款免责条款，并将上下文不足、OCR 破损或存在合理分歧的样本排除；当前共 51 个重点核查样本点、24 个无明显风险样本和 20 个排除项。正负样本统一使用中性问题，避免把风险类型写进提问造成标签泄漏。2026-10-01 单次完整 Agent 运行结果：

| 指标 | 结果 |
|---|---:|
| Top-5 金标法条命中 | 51 / 51（100%） |
| 检索 MRR | 0.485 |
| Agent 金标法条命中 | 36 / 51（70.6%） |
| 风险识别 Precision / Recall / F1 | 90.4% / 92.2% / 91.3% |
| 负样本特异度 / 误报率 | 75.0% / 20.8% |
| 严格准确率 | 65 / 75（86.7%） |
| 判定协议遵循率 | 71 / 75（94.7%） |
| 无效引用 / 无本轮依据引用 | 0 / 3 |

错误分析驱动的风险阈值校准把“可以写得更细”与“已出现实质风险信号”分开，并禁止从短条款未展示完整合同内容推断合同缺项。本次 pgvector 完整运行的 24 个负例中有 5 个误报；Agent 调用具有非确定性，检索指标用于判断迁移是否退化，分类指标以逐次报告为准。完整逐条结果见 [公开条款评测报告](sample_contracts/public_clause_benchmark/eval_report.json)，数据来源与标注边界见 [评测集说明](sample_contracts/public_clause_benchmark/README.md)。

### 合成合同回归集

8 份合成合同包含 27 个预设风险点和 19 条独立金标法条，用于监控工程链路是否退化：

| 指标 | 结果（每份运行 3 次） |
|---|---:|
| 风险点召回率 | 80% |
| 金标法条召回率 | 98% |
| 金标法条精确率 | 42% |
| 确定性引用校验 | 8 / 8 通过 |

> 评测边界：公开集标签为基准维护者的单轮策展标注，不是双人律师金标；同一合同拆出的多个样本也不满足完全独立同分布假设。合成集只用于回归。以上结果不等同于真实合同风险识别准确率或法律意见准确率。

## 快速开始

### Docker（推荐）

环境要求：Docker Desktop，或 Docker Engine + Compose Plugin。

~~~powershell
Copy-Item .env.example .env
# 编辑 .env，至少填写 DEEPSEEK_API_KEY、INIT_PASSWORD、JWT_SECRET、
# POSTGRES_PASSWORD 和 RABBITMQ_PASSWORD

docker compose up --build
~~~

首次启动会下载约 95 MB 的向量模型，执行 Alembic 迁移，并把 1,023 条法律语料幂等同步到 pgvector。完成后打开 http://127.0.0.1:8000；若端口已占用，可先设置 `APP_PORT=8001`。

~~~powershell
docker compose up -d        # 后台启动
docker compose logs -f app  # 查看启动进度
docker compose logs -f worker
docker compose down         # 停止服务，保留数据和模型
~~~

### 本地开发

需要 Python 3.12、Node.js 18+ 和 DeepSeek API Key。

~~~powershell
conda create --prefix .\.venv python=3.12 pip -y
conda activate .\.venv
python -m pip install -r backend/requirements.txt

python -m backend.scripts.download_model
python -m backend.app.core.chunking

# 后端
python -m backend.app.api.main

# 另开终端启动前端
cd frontend
npm install
npm run dev
~~~

前端默认运行在 http://127.0.0.1:5173，并将 API 请求代理到 8000 端口。本地不启动 RabbitMQ/Worker 时仍可使用同步 `/api/chat`；完整异步审查链路建议使用 Compose。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3、Vite |
| 后端 | FastAPI、Uvicorn、SQLAlchemy、PostgreSQL、Alembic |
| 异步任务 | RabbitMQ、Celery、Transactional Outbox、任务租约与幂等消费 |
| 限流与观测 | Redis、Prometheus、JSON Structured Logging |
| Agent | OpenAI-compatible Function Calling、DeepSeek API |
| 检索 | pgvector 精确余弦检索、bge-small-zh-v1.5、BM25、jieba、RRF；FAISS 仅作本地回归对照 |
| 可选精排 | BAAI/bge-reranker-base |
| 安全 | Argon2、JWT、证据白名单、HTML 转义 |

## 项目结构

~~~text
frontend/               Vue 3 用户界面
backend/
  app/
    agent/              Agent 循环、工具、提示词与上下文管理
    api/                FastAPI 接口
    core/               分块、向量/BM25 检索、融合与引用校验
    infra/              SQLAlchemy 模型、认证、限流、任务与可观测性
    services/           文件解析与文档导出
    worker/             Celery 应用与合同审查消费者
  migrations/           Alembic 数据库迁移
  scripts/              语料准备、验收与离线评测
corpus/                 法律原文
sample_contracts/       合成合同与公开条款评测集
docker/                 容器启动脚本
~~~

## 语料与限制

本地语料包含 8 部法律及司法解释，共 1,023 个法条块，覆盖《民法典》合同编、合同编通则解释、买卖合同解释，以及劳动和社会保险相关法律。法律文件、修订版本、效力状态、生效日期、法条元数据和 512 维向量统一存储在 PostgreSQL；当前规模采用精确余弦检索，未创建 HNSW/IVFFlat 近似索引。

- 不含案例库、地方性法规、部门规章和跨法域材料。
- 法律更新需要人工同步源文件并执行 `python -m backend.scripts.sync_legal_corpus`；同步按稳定 `chunk_key` 幂等更新。
- 扫描版 PDF 暂不支持 OCR，需要先转换为可提取文本。
- 复杂事实认定、争议策略和最终法律意见仍需专业人员判断。

## 测试与评测

~~~powershell
python -m backend.scripts.verify_retrieval
python -m backend.scripts.verify_citations
python -m backend.scripts.verify_session_contract
python -m backend.scripts.verify_agent_engineering
python -m backend.scripts.verify_review_jobs
python -m pytest

# 查看迁移状态
python -m alembic current

# 校验/幂等同步 PostgreSQL 法律语料
python -m backend.scripts.sync_legal_corpus --check
python -m backend.scripts.sync_legal_corpus

# 公开条款检索评测；添加 --agent 执行真实端到端评测
python -X utf8 -m backend.scripts.eval_public_clauses
python -X utf8 -m backend.scripts.eval_public_clauses --agent
~~~

系统定位为合同初审辅助工具：检索不到直接依据时明确说明，不使用模型常识补造法条，也不自动替代、修改或签署合同。

运行后可用 `/health` 检查进程存活、`/ready` 检查 PostgreSQL/RabbitMQ/Redis 依赖，Prometheus 指标暴露在 `/metrics`。
