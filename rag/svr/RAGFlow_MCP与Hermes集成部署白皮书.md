# RAGFlow MCP Server 与 Hermes 集成部署白皮书

## 一、概述

RAGFlow MCP Server (`mcp_server.py`) 是一个零外部依赖、纯标准库实现的 MCP (Model Context Protocol) JSON-RPC 服务，通过 stdio 传输协议将 RAGFlow 的 AI 能力暴露给 Hermes AI Agent 框架使用。

### 核心信息

| 项目 | 值 |
|------|-----|
| MCP Server 路径 | `/home/bid-agent-konus/ragflow2/rag/svr/mcp_server.py` |
| 容器内路径 | `/ragflow/rag/svr/mcp_server.py` |
| Python 版本 | Python 3 (容器内 `python3`) |
| 传输协议 | JSON-RPC 2.0 over stdio |
| 依赖 | 无第三方库，仅标准库 (json, sys, os, re, ssl, urllib) |

---

## 二、暴露的工具

### 2.1 `ask_agent` — AI Agent 问答

向 RAGFlow 的 Agent Canvas 发送问题并获取 AI 回答。

```json
{
  "name": "ask_agent",
  "arguments": {
    "api_key": "ragflow-xxx",
    "agent_id": "canvas-id",
    "question": "你的问题",
    "session_id": ""
  }
}
```

- 支持 SSE 流式响应解析
- 自动处理 `<think>` 标签（DeepSeek R1 推理模型）
- 可选 `session_id` 维持对话上下文

### 2.2 `search_bid_projects` — 标讯项目搜索

从 MySQL `bid_project` 表搜索中国政府招投标项目。

```json
{
  "name": "search_bid_projects",
  "arguments": {
    "api_key": "ragflow-xxx",
    "keyword": "道路工程",
    "project_class_id": "GC",
    "provice_code": "350000",
    "start_date": "2026-05-01",
    "end_date": "2026-05-26",
    "page": "1"
  }
}
```

支持 14 个筛选参数：keyword、project_class_id、purchase_type_id、provice_code、city_code、start_date、end_date、project_money_min/max、part_a_name、part_b_name、industry_code、page、items_per_page。

---

## 三、Hermes 配置

### 3.1 配置文件位置

```
/root/.hermes/config.yaml
```

### 3.2 MCP 服务器配置段

```yaml
mcp_servers:
  ragflow:
    command: python3
    args:
      - /home/bid-agent-konus/ragflow2/rag/svr/mcp_server.py
```

**关键注意事项：**

1. **路径必须是宿主机绝对路径** — Hermes 在宿主机运行，不要使用容器内路径 `/ragflow/rag/...`
2. **`python3` 必须可用** — 确认 `which python3` 可找到
3. **环境变量** — 如需指定 RAGFlow API 地址，在 `env` 字段设置：

```yaml
mcp_servers:
  ragflow:
    command: python3
    args:
      - /home/bid-agent-konus/ragflow2/rag/svr/mcp_server.py
    env:
      RAGFLOW_BASE_URL: http://127.0.0.1:9380/api/v1
```

- `RAGFLOW_BASE_URL` 默认值为 `http://127.0.0.1:9380/api/v1`，若容器端口映射未变更则无需显式设置

---

## 四、部署检查清单

### 4.1 服务器重新部署后必检项

- [ ] **代码已同步**：确保 `mcp_server.py` 存在于 `/home/bid-agent-konus/ragflow2/rag/svr/`
- [ ] **容器正在运行**：`docker ps | grep ragflow` 确认 ragflow-server 容器运行中
- [ ] **API 端口可达**：`curl -s http://127.0.0.1:9380/api/v1/ | head -c 200` 返回正常 JSON
- [ ] **Python3 可用**：`python3 --version` 应输出 Python 3.x
- [ ] **Hermes 已安装**：`which hermes` 或 `/usr/local/bin/hermes --version`
- [ ] **MCP 配置已写入**：`cat /root/.hermes/config.yaml | grep -A3 ragflow` 确认 mcp_servers 段存在
- [ ] **重启 Hermes**：修改配置后需重启 Hermes 服务

### 4.2 首次部署步骤

```bash
# 1. 确认文件存在
ls -la /home/bid-agent-konus/ragflow2/rag/svr/mcp_server.py

# 2. 确保可执行（可选，非必须）
chmod +x /home/bid-agent-konus/ragflow2/rag/svr/mcp_server.py

# 3. 测试 MCP 手动连接
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python3 /home/bid-agent-konus/ragflow2/rag/svr/mcp_server.py

# 4. 检查 Hermes 配置
cat /root/.hermes/config.yaml

# 5. 重启 Hermes
systemctl restart hermes   # 或对应的服务管理命令
```

---

## 五、验证命令

### 5.1 手动测试 MCP Server

```bash
# 测试 initialize
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | \
  python3 /home/bid-agent-konus/ragflow2/rag/svr/mcp_server.py
# 预期输出: {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", ...}}

# 测试 tools/list
echo '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | \
  python3 /home/bid-agent-konus/ragflow2/rag/svr/mcp_server.py
# 预期输出: 包含 ask_agent 和 search_bid_projects 两个工具定义
```

### 5.2 验证 Hermes 已加载 MCP 工具

在 Hermes 对话中询问：
> "你有哪些可用的 MCP 工具？"

应能看到 `ask_agent` 和 `search_bid_projects`。

---

## 六、API Key 说明

两个 MCP 工具都需要 `api_key` 参数，格式为 `ragflow-xxx`。

**获取方式：**
1. 登录 RAGFlow 管理面板 (http://<服务器IP>:9380)
2. 进入 **API 密钥** 页面
3. 创建或复制已有的 API Key

**在 Hermes 中使用：**
- Hermes 调用 MCP 工具时，需要由用户或系统提示词提供 API Key
- 建议在 Hermes 的 system prompt 中预设常用 API Key

---

## 七、常见问题排查

| 问题 | 可能原因 | 解决方法 |
|------|---------|---------|
| Hermes 找不到 MCP 工具 | 配置未生效 | 重启 Hermes，检查 config.yaml 缩进 |
| `Connection refused` | RAGFlow 容器未运行 | `docker start docker-ragflow-cpu-1` |
| MCP 调用超时 | SSE 流式响应超时 | 检查 Agent Canvas 是否正常，增加 timeout |
| `ModuleNotFoundError` | Hermes 使用系统 Python 而非容器 Python | MCP server 无第三方依赖，标准库足够 |
| 中文乱码 | 终端编码问题 | MCP server 内部使用 `ensure_ascii=False`，确保 LANG=en_US.UTF-8 |
| `api_key` 认证失败 | API Key 无效或过期 | 在 RAGFlow 面板重新生成 API Key |

---

## 八、关键路径速查

| 用途 | 路径 |
|------|------|
| MCP Server 源码 | `D:\AI\ragflow2\rag\svr\mcp_server.py` |
| 宿主机代码目录 | `/home/bid-agent-konus/ragflow2/rag/svr/` |
| 容器内代码目录 | `/ragflow/rag/svr/` |
| Hermes 配置文件 | `/root/.hermes/config.yaml` |
| Hermes 二进制 | `/usr/local/bin/hermes` |
| 本项目集成文档 | `reference_mcp_integration.md` |

---

## 九、容器与宿主机路径映射

```
宿主机: /home/bid-agent-konus/ragflow2/rag  →  容器内: /ragflow/rag
```

部署代码时使用 SCP 上传到宿主机路径即可，容器内自动可见（bind mount）。

---

*文档版本: 2026-05-26*
