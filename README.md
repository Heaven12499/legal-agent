# 合同审查助手：可追溯法律检索 RAG

面向合同法务初审场景：上传合同或直接提问，系统定位待核查条款，通过混合检索找到相关法律原文，并输出带证据引用的风险说明。

项目重点不是让模型“生成一段法律回答”，而是把合同条款稳定映射到可核验的法律依据，并对最终引用进行真实性和本轮证据校验。输出仅供人工初审参考，不替代律师判断。

> 核心链路：问题路由 → Query Rewrite → Vector + BM25 + RRF → 相邻法条扩展 → Evidence Whitelist → Citation Verification → Limited Reflection

---

## 核心能力

- **合同条款核查**：识别违约金、格式条款、竞业限制等重点条款，逐项给出风险说明和法律依据。
- **混合检索**：结合 bge-small-zh 向量召回与 BM25 词面匹配，通过 RRF 融合结果；可选 Cross-Encoder reranker。
- **可追溯引用**：每处法律引用必须真实存在于本地语料，且来自本轮检索证据；异常引用会被纠错或明确标警告。
- **Agent 工具调用**：单智能体自主决定检索、精确查询法条或直接回答，设置最大轮次防止无限循环，并记录完整 trace。
- **完整 Web 体验**：支持 docx、PDF、TXT 上传，多轮会话、消息修改与重新生成、SQLite 持久化，以及 JWT 多用户隔离。
- **可复现评测**：合成合同用于回归测试，公开合同短条款用于外部验证，两类结果分开报告。

## 系统流程

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

检索与生成过程会记录查询改写、工具调用、命中文档和引用校验结果，便于在前端追踪答案依据。

## 评测结果

### 公开合同短条款集

主评测集来自 10 份政府采购公开合同，裁出 28 条二次脱敏短条款并标注 30 个重点核查点。2026-09-05 单次完整 Agent 运行结果：

| 指标 | 结果 |
|---|---:|
| Top-5 金标法条命中 | 30 / 30（100%） |
| 检索 MRR | 0.375 |
| Agent 金标法条命中 | 30 / 30（100%） |
| 无效引用 | 0 |
| 无本轮依据引用 | 1 |

其中 1 处无本轮依据引用已被证据白名单校验器识别并在答案中标注警告。完整逐条结果见 [公开条款评测报告](sample_contracts/public_clause_benchmark/eval_report.json)，数据来源与标注边界见 [评测集说明](sample_contracts/public_clause_benchmark/README.md)。

### 合成合同回归集

8 份合成合同包含 27 个预设风险点和 19 条独立金标法条，用于监控工程链路是否退化：

| 指标 | 结果（每份运行 3 次） |
|---|---:|
| 风险点召回率 | 80% |
| 金标法条召回率 | 98% |
| 金标法条精确率 | 42% |
| 确定性引用校验 | 8 / 8 通过 |

> 评测边界：公开集查询经过法律语义标注，当前负样本尚未自动计入精确率；合成集只用于回归。以上结果衡量检索和引用链路，不等同于真实合同风险识别准确率或法律意见准确率。

## 快速开始

### Docker（推荐）

环境要求：Docker Desktop，或 Docker Engine + Compose Plugin。

~~~powershell
Copy-Item .env.example .env
# 编辑 .env，填写 DEEPSEEK_API_KEY、INIT_PASSWORD、JWT_SECRET

docker compose up --build
~~~

首次启动会下载约 95 MB 的向量模型并生成索引。完成后打开 http://127.0.0.1:8000。

~~~powershell
docker compose up -d        # 后台启动
docker compose logs -f app  # 查看启动进度
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

前端默认运行在 http://127.0.0.1:5173，并将 API 请求代理到 8000 端口。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3、Vite |
| 后端 | FastAPI、Uvicorn、SQLite |
| Agent | OpenAI-compatible Function Calling、DeepSeek API |
| 检索 | FAISS、bge-small-zh-v1.5、BM25、jieba、RRF |
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
    infra/              认证与会话持久化
    services/           文件解析与文档导出
  scripts/              语料准备、验收与离线评测
corpus/                 法律原文
sample_contracts/       合成合同与公开条款评测集
docker/                 容器启动脚本
~~~

## 语料与限制

本地语料包含 8 部法律及司法解释，共 1023 个法条块，覆盖《民法典》合同编、合同编通则解释、买卖合同解释，以及劳动和社会保险相关法律。

- 不含案例库、地方性法规、部门规章和跨法域材料。
- 法律更新需要人工同步语料并重建索引。
- 扫描版 PDF 暂不支持 OCR，需要先转换为可提取文本。
- 复杂事实认定、争议策略和最终法律意见仍需专业人员判断。

## 测试与评测

~~~powershell
python -m backend.scripts.verify_retrieval
python -m backend.scripts.verify_citations
python -m backend.scripts.verify_session_contract
python -m backend.scripts.verify_agent_engineering

# 公开条款检索评测；添加 --agent 执行真实端到端评测
python -X utf8 -m backend.scripts.eval_public_clauses
python -X utf8 -m backend.scripts.eval_public_clauses --agent
~~~

系统定位为合同初审辅助工具：检索不到直接依据时明确说明，不使用模型常识补造法条，也不自动替代、修改或签署合同。
