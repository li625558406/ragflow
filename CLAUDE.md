# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RAGFlow is an open-source RAG (Retrieval-Augmented Generation) engine based on deep document understanding. It's a full-stack application with:

- Python backend (Flask-based API server)
- React/TypeScript frontend (built with vitejs)
- Microservices architecture with Docker deployment
- Multiple data stores (MySQL, Elasticsearch/Infinity, Redis, MinIO)

## Architecture

### Backend (`/api/`)

- **Main Server**: `api/ragflow_server.py` - Flask application entry point
- **Apps**: Modular Flask blueprints in `api/apps/` for different functionalities:
  - `kb_app.py` - Knowledge base management
  - `dialog_app.py` - Chat/conversation handling
  - `document_app.py` - Document processing
  - `canvas_app.py` - Agent workflow canvas
  - `file_app.py` - File upload/management
- **Services**: Business logic in `api/db/services/`
- **Models**: Database models in `api/db/db_models.py`

### Core Processing (`/rag/`)

- **Document Processing**: `deepdoc/` - PDF parsing, OCR, layout analysis
- **LLM Integration**: `rag/llm/` - Model abstractions for chat, embedding, reranking
- **RAG Pipeline**: `rag/flow/` - Chunking, parsing, tokenization
- **Graph RAG**: `rag/graphrag/` - Knowledge graph construction and querying

### Agent System (`/agent/`)

- **Components**: Modular workflow components (LLM, retrieval, categorize, etc.)
- **Templates**: Pre-built agent workflows in `agent/templates/`
- **Tools**: External API integrations (Tavily, Wikipedia, SQL execution, etc.)

### Frontend (`/web/`)

- React/TypeScript with vitejs framework
- shadcn/ui components
- State management with Zustand
- Tailwind CSS for styling

## Common Development Commands

### Backend Development

```bash
# Install Python dependencies
uv sync --python 3.12 --all-extras
uv run python3 download_deps.py
pre-commit install

# Start dependent services
docker compose -f docker/docker-compose-base.yml up -d

# Run backend (requires services to be running)
source .venv/bin/activate
export PYTHONPATH=$(pwd)
bash docker/launch_backend_service.sh

# Run tests
uv run pytest

# Linting
ruff check
ruff format
```

### Frontend Development

```bash
cd web
npm install
npm run dev        # Development server
npm run build      # Production build
npm run lint       # ESLint
npm run test       # Jest tests
```

### Docker Development

```bash
# Full stack with Docker
cd docker
docker compose -f docker-compose.yml up -d

# Check server status
docker logs -f ragflow-server

# Rebuild images
docker build --platform linux/amd64 -f Dockerfile -t infiniflow/ragflow:nightly .
```

## Key Configuration Files

- `docker/.env` - Environment variables for Docker deployment
- `docker/service_conf.yaml.template` - Backend service configuration
- `pyproject.toml` - Python dependencies and project configuration
- `web/package.json` - Frontend dependencies and scripts

## Testing

- **Python**: pytest with markers (p1/p2/p3 priority levels)
- **Frontend**: Jest with React Testing Library
- **API Tests**: HTTP API and SDK tests in `test/` and `sdk/python/test/`

## Database Engines

RAGFlow supports switching between Elasticsearch (default) and Infinity:

- Set `DOC_ENGINE=infinity` in `docker/.env` to use Infinity
- Requires container restart: `docker compose down -v && docker compose up -d`

## Development Environment Requirements

- Python 3.10-3.12
- Node.js >=18.20.4
- Docker & Docker Compose
- uv package manager
- 16GB+ RAM, 50GB+ disk space

1. Think before acting. Read existing files before writing code.
2. Be concise in output but thorough in reasoning.
3. Prefer editing over rewriting whole files.
4. Do not re-read files you have already read.
5. Test your code before declaring done.
6. No sycophantic openers or closing fluff.
7. Keep solutions simple and direct.
8. User instructions always override this file.

## konus Skills (二次开发规范)

本项目有一套 konus 技能系统，位于 `skills/` 目录。当用户提到 **`ragflow`** 时，激活 `konus` 技能进行意图分析，自动路由到对应子技能。

### 技能清单

| 技能 | 用途 | 触发关键词 |
|------|------|-----------|
| `konus` | 意图路由器 + 项目上下文 | ragflow, 项目, 开发, 代码 |
| `konus-project-context` | 技术栈、目录结构、约定 | 项目结构, 技术栈, 目录 |
| `konus-backend-dev` | 后端 API/ORM/Service 开发 | 接口, API, 后端, Service, 数据库 |
| `konus-frontend-dev` | 前端页面/组件/样式开发 | 页面, 组件, 前端, UI, 样式 |
| `konus-crawler-dev` | 爬虫站点配置/修复 | 爬虫, 站点, 采集, YAML |
| `konus-code-review` | 代码审查 (开发后强制) | 审查, review, 检查, 测试 |
| `konus-deploy-guide` | 部署规范 | 部署, 上传, 服务器, 更新 |

### 核心规则

1. 任何代码开发完成后，**必须** 调用 `konus-code-review` 审查
2. **禁止自动重启服务**，需重启时报告用户
3. 前端开发**必须适配当前风格**（深色主题、卡片布局）
4. **禁止修改上游核心文件**（ragflow_server.py、db_models.py、pipeline.py），除非用户确认

### 完整项目文档

详细架构文档见 `F:\投标项目\AI\项目架构.md`
