# 标书分析助手 - 新电脑部署指南

## 一、环境准备

### 1.1 硬件要求

| 项目 | 最低要求 | 推荐配置 |
|------|---------|---------|
| 内存 | 16 GB | 32 GB |
| 磁盘 | 50 GB 可用空间 | 100 GB SSD |
| CPU | 4 核 | 8 核 |
| 系统 | Windows 10/11 Pro/Enterprise | Windows 11 |

### 1.2 安装 Docker Desktop

1. 下载 Docker Desktop：https://www.docker.com/products/docker-desktop/
2. 安装时勾选 **Use WSL 2 instead of Hyper-V**
3. 安装完成后重启电脑
4. 打开 Docker Desktop → Settings → Resources：
   - **Memory**：至少 8 GB（推荐 12 GB）
   - **Disk**：至少 60 GB
5. 确认 Docker 运行正常：终端执行 `docker --version`

### 1.3 安装 Git

1. 下载 Git：https://git-scm.com/download/win
2. 安装时保持默认选项即可

---

## 二、获取项目代码

### 2.1 克隆 RAGFlow

```bash
cd D:\
git clone https://github.com/infiniflow/ragflow.git
cd ragflow
git checkout v0.25.1
```

### 2.2 下载自定义页面文件

将以下两个文件放到 `D:\AI\ragflow\docker\nginx\html\` 目录下：

- `index.html` — 首页
- `agent-chat.html` — 聊天页面

> 这两个文件从当前已部署的电脑复制，路径：`D:\AI\ragflow\docker\nginx\html\`

---

## 三、修改配置文件（共 5 个文件）

### 3.1 修改 `docker/.env`

在文件末尾添加管理端口：

```env
ADMIN_WEB_PORT=18082
```

完整上下文（找到对应位置修改）：

```env
SVR_WEB_HTTP_PORT=80
SVR_WEB_HTTPS_PORT=443
SVR_HTTP_PORT=9380
ADMIN_SVR_HTTP_PORT=9381
SVR_MCP_PORT=9382
GO_HTTP_PORT=9384
GO_ADMIN_PORT=9383
ADMIN_WEB_PORT=18082
```

### 3.2 修改 `docker/docker-compose-base.yml`

修改 `es01` 服务的内存配置和健康检查：

```yaml
  es01:
    profiles:
      - elasticsearch
    image: elasticsearch:${STACK_VERSION}
    volumes:
      - esdata01:/usr/share/elasticsearch/data
    ports:
      - ${ES_PORT}:9200
    env_file: .env
    environment:
      - node.name=es01
      - ELASTIC_PASSWORD=${ELASTIC_PASSWORD}
      - bootstrap.memory_lock=false
      - discovery.type=single-node
      - xpack.security.enabled=true
      - xpack.security.http.ssl.enabled=false
      - xpack.security.transport.ssl.enabled=false
      - cluster.routing.allocation.disk.watermark.low=5gb
      - cluster.routing.allocation.disk.watermark.high=3gb
      - cluster.routing.allocation.disk.watermark.flood_stage=2gb
      - ES_JAVA_OPTS=-Xms2g -Xmx2g
    mem_limit: 4g
    ulimits:
      memlock:
        soft: -1
        hard: -1
    healthcheck:
      test: ["CMD-SHELL", "curl -sf -u elastic:${ELASTIC_PASSWORD} http://localhost:9200/_cluster/health | grep -q '\"status\":\"green\\|yellow\"'"]
      interval: 10s
      timeout: 10s
      retries: 120
    networks:
      - ragflow
    restart: unless-stopped
```

**改动点：**
- 添加 `ES_JAVA_OPTS=-Xms2g -Xmx2g`（限制 JVM 堆内存为 2GB）
- 修改 `mem_limit: 4g`（容器内存限制 4GB）
- 修改 healthcheck 为验证集群健康状态（`green\|yellow`）

### 3.3 修改 `docker/docker-compose.yml`

完整替换 `ragflow-cpu` 和 `ragflow-gpu` 两个服务：

```yaml
include:
  - ./docker-compose-base.yml
services:
  ragflow-cpu:
    depends_on:
      mysql:
        condition: service_healthy
      es01:
        condition: service_healthy
    profiles:
      - cpu
    image: ${RAGFLOW_IMAGE}
    command:
      - --enable-adminserver
    ports:
      - ${SVR_WEB_HTTP_PORT}:80
      - ${SVR_WEB_HTTPS_PORT}:443
      - ${SVR_HTTP_PORT}:9380
      - ${ADMIN_SVR_HTTP_PORT}:9381
      - ${SVR_MCP_PORT}:9382
      - ${GO_HTTP_PORT}:9384
      - ${GO_ADMIN_PORT}:9383
      - ${ADMIN_WEB_PORT}:18082
    volumes:
      - ./ragflow-logs:/ragflow/logs
      - ./nginx/ragflow.conf.python:/etc/nginx/conf.d/ragflow.conf.python
      - ./service_conf.yaml.template:/ragflow/conf/service_conf.yaml.template
      - ./entrypoint.sh:/ragflow/entrypoint.sh
      - ../common/doc_store/es_conn_pool.py:/ragflow/common/doc_store/es_conn_pool.py
      - ./nginx/html/agent-chat.html:/ragflow/web/custom/chat.html
      - ./nginx/html/index.html:/ragflow/web/custom/landing.html
    env_file: .env
    networks:
      - ragflow
    restart: unless-stopped
    extra_hosts:
      - "host.docker.internal:host-gateway"

  ragflow-gpu:
    depends_on:
      mysql:
        condition: service_healthy
      es01:
        condition: service_healthy
    profiles:
      - gpu
    image: ${RAGFLOW_IMAGE}
    command:
      - --enable-adminserver
    ports:
      - ${SVR_WEB_HTTP_PORT}:80
      - ${SVR_WEB_HTTPS_PORT}:443
      - ${SVR_HTTP_PORT}:9380
      - ${ADMIN_SVR_HTTP_PORT}:9381
      - ${SVR_MCP_PORT}:9382
      - ${GO_HTTP_PORT}:9384
      - ${GO_ADMIN_PORT}:9383
      - ${ADMIN_WEB_PORT}:18082
    volumes:
      - ./ragflow-logs:/ragflow/logs
      - ./nginx/ragflow.conf.python:/etc/nginx/conf.d/ragflow.conf.python
      - ./service_conf.yaml.template:/ragflow/conf/service_conf.yaml.template
      - ./entrypoint.sh:/ragflow/entrypoint.sh
      - ../common/doc_store/es_conn_pool.py:/ragflow/common/doc_store/es_conn_pool.py
      - ./nginx/html/agent-chat.html:/ragflow/web/custom/chat.html
      - ./nginx/html/index.html:/ragflow/web/custom/landing.html
    env_file: .env
    networks:
      - ragflow
    restart: unless-stopped
    extra_hosts:
      - "host.docker.internal:host-gateway"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

**改动点：**
- 添加 `es01` 依赖（`depends_on`）
- 添加 `--enable-adminserver` 命令
- 添加 `ADMIN_WEB_PORT:18082` 端口映射
- 添加 `GO_HTTP_PORT` 和 `GO_ADMIN_PORT` 端口映射
- 挂载 `ragflow.conf.python` 到容器
- 挂载 HTML 文件到 `/ragflow/web/custom/`（不再覆盖 dist 目录）
- 挂载 `es_conn_pool.py`

### 3.4 替换 `docker/nginx/ragflow.conf.python`

**整个文件替换**为以下内容：

```nginx
# =============================================================================
# User-facing server (port 80) - Landing page + Chat
# =============================================================================
server {
    listen 80;
    server_name _;
    root /ragflow/web/dist;

    gzip on;
    gzip_min_length 1k;
    gzip_comp_level 9;
    gzip_types text/plain application/javascript application/x-javascript text/css application/xml text/javascript application/x-httpd-php image/jpeg image/gif image/png;
    gzip_vary on;
    gzip_disable "MSIE [1-6]\.";

    # API routes - needed for chat functionality
    location ~ ^/api/v1/admin {
        proxy_pass http://localhost:9381;
        include proxy.conf;
    }

    location ~ ^/(v1|api) {
        proxy_pass http://localhost:9380;
        include proxy.conf;
    }

    # Landing page
    location = / {
        root /ragflow/web/custom;
        try_files /landing.html =404;
    }

    # Chat page
    location = /chat {
        root /ragflow/web/custom;
        try_files /chat.html =404;
    }

    # Block all other paths on user port
    location / {
        return 404;
    }
}

# =============================================================================
# RAGFlow Admin server (port 18082) - Management interface
# =============================================================================
server {
    listen 18082;
    server_name _;
    root /ragflow/web/dist;

    gzip on;
    gzip_min_length 1k;
    gzip_comp_level 9;
    gzip_types text/plain application/javascript application/x-javascript text/css application/xml text/javascript application/x-httpd-php image/jpeg image/gif image/png;
    gzip_vary on;
    gzip_disable "MSIE [1-6]\.";

    location ~ ^/api/v1/admin {
        proxy_pass http://localhost:9381;
        include proxy.conf;
    }

    location ~ ^/(v1|api) {
        proxy_pass http://localhost:9380;
        include proxy.conf;
    }

    location / {
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    # Cache-Control: max-age Expires
    location ~ ^/static/(css|js|media)/ {
        expires 10y;
        access_log off;
    }
}
```

### 3.5 修改 `common/doc_store/es_conn_pool.py`

替换 `_connect` 方法，添加重试和嗅探：

```python
    def _connect(self):
        self.es_conn = Elasticsearch(
            self.ES_CONFIG["hosts"].split(","),
            basic_auth=(self.ES_CONFIG["username"], self.ES_CONFIG[
                "password"]) if "username" in self.ES_CONFIG and "password" in self.ES_CONFIG else None,
            verify_certs= self.ES_CONFIG.get("verify_certs", False),
            timeout=600,
            max_retries=3,
            retry_on_timeout=True,
            sniff_on_connection_fail=True,
            sniff_timeout=10,
        )
        if self.es_conn:
            self.info = self.es_conn.info()
            return True
        return False
```

**改动点：** 添加了 `max_retries=3`、`retry_on_timeout=True`、`sniff_on_connection_fail=True`、`sniff_timeout=10`

---

## 四、替换 Agent ID

在 `index.html` 和 `agent-chat.html` 中搜索 `SET_YOUR_AGENT_ID`，全部替换为你的实际智能体 ID。

获取方式：登录 RAGFlow 管理后台 → 智能体 → 点击你的标书分析智能体 → 浏览器地址栏中的 ID（UUID 格式）。

> **注意**：新电脑上需要先启动项目、创建好智能体后，才能获取 Agent ID。可以先用默认值启动，创建智能体后再替换，最后重启容器。

---

## 五、启动项目

```bash
cd D:\AI\ragflow\docker
docker compose up -d
```

首次启动需要拉取镜像，约 10-20 分钟（取决于网速）。

### 查看启动进度

```bash
docker logs -f docker-ragflow-cpu-1
```

看到以下输出表示启动成功：

```
Applied nginx config: ragflow.conf.python
Starting nginx...
Attempt to start RAGFlow server...
```

### 验证服务

```bash
# 检查首页（应返回 200）
curl -s -o /dev/null -w "%{http_code}" http://localhost/

# 检查管理后台（应返回 200）
curl -s -o /dev/null -w "%{http_code}" http://localhost:18082/

# 检查聊天页面（应返回 200）
curl -s -o /dev/null -w "%{http_code}" http://localhost/chat

# 检查后端 API（应返回版本信息）
curl -s http://localhost:18082/api/v1/system/version
```

---

## 六、初始化配置

### 6.1 创建管理员账号

1. 浏览器访问 `http://localhost:18082/`
2. 注册一个管理员账号（第一个注册的用户自动成为超级管理员）

### 6.2 配置 LLM 模型

在管理后台中配置你的大语言模型（如 Ollama 本地模型或第三方 API）：

1. 进入 **系统设置 → 模型供应商**
2. 添加模型（如 Ollama、OpenAI 兼容 API 等）
3. 设置默认的 Chat 模型和 Embedding 模型

### 6.3 创建标书分析智能体

1. 进入 **智能体** 页面
2. 创建新的智能体，配置：
   - 关联知识库（上传标书文件）
   - 设置 Prompt（标书分析相关提示词）
   - 发布智能体
3. 从浏览器地址栏复制 **Agent ID**
4. 替换 `index.html` 和 `agent-chat.html` 中的 `SET_YOUR_AGENT_ID`
5. 重启容器：`docker compose restart ragflow-cpu`

---

## 七、访问地址

| 角色 | 地址 | 说明 |
|------|------|------|
| 普通用户 | `http://IP/` | 首页 |
| 普通用户 | `http://IP/chat` | 聊天页面 |
| 管理员 | `http://IP:18082/` | RAGFlow 管理后台 |

> `IP` 替换为实际的内网 IP 地址。其他同事通过 `http://IP/chat` 即可使用聊天功能。

---

## 八、常用运维命令

```bash
# 启动
docker compose up -d

# 停止
docker compose down

# 重启
docker compose restart ragflow-cpu

# 查看日志
docker logs -f docker-ragflow-cpu-1

# 重建容器（配置文件修改后）
docker compose up -d --force-recreate ragflow-cpu

# 查看所有容器状态
docker ps
```

---

## 九、文件清单

所有自定义修改的文件汇总：

| 文件路径 | 修改内容 |
|---------|---------|
| `docker/.env` | 添加 `ADMIN_WEB_PORT=18082` |
| `docker/docker-compose-base.yml` | ES 内存限制 4GB、JVM 2GB、健康检查增强 |
| `docker/docker-compose.yml` | 双端口分离、nginx 配置挂载、HTML 挂载路径 |
| `docker/nginx/ragflow.conf.python` | 双 server block（80 用户 / 18082 管理） |
| `docker/nginx/html/index.html` | 首页（自定义） |
| `docker/nginx/html/agent-chat.html` | 聊天页面（自定义） |
| `common/doc_store/es_conn_pool.py` | ES 连接重试和嗅探 |
