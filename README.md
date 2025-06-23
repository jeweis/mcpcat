# MCPCat

一个用Python实现的MCP（Model Context Protocol）聚合平台，支持多种MCP协议的统一管理和运行。

## 功能特性

- 🚀 基于FastAPI构建的高性能Web服务
- 🔌 支持多种MCP协议：
  - STDIO (默认，用于本地工具)
  - Streamable HTTP (推荐用于Web服务)
  - SSE (传统Web传输)
- 📊 统一的MCP服务管理界面
- 🛡️ 安全的服务隔离和权限控制

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行服务

```bash
python main.py
```

或使用uvicorn：

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 访问服务

- 控制台页面（前端）: http://localhost:8000
- API文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/health
- streamable http访问端点：http://{本机ip}:8000/sse/{Mcp Server名称}
- sse访问端点：http://{本机ip}:8000/mcp/{Mcp Server名称}

## Docker 部署

### 使用预构建镜像（推荐）

#### 快速启动

```bash
docker run -d \
  --name mcpcat-app \
  -p 8000:8000 \
  -v $(pwd)/config.json:/app/config.json:ro \
  -v $(pwd)/logs:/app/logs \
  --restart unless-stopped \
  jeweis/mcpcat:latest
```

#### 完整配置启动

```bash
docker run -d \
  --name mcpcat-app \
  -p 8000:8000 \
  -e APP_NAME=MCPCat \
  -e APP_VERSION=0.1.1 \
  -e HOST=0.0.0.0 \
  -e PORT=8000 \
  -e LOG_LEVEL=INFO \
  -e MCPCAT_CONFIG_PATH=/app/config.json \
  -v $(pwd)/config.json:/app/config.json:ro \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/data:/app/data \
  --restart unless-stopped \
  --health-cmd="curl -f http://localhost:8000/api/health" \
  --health-interval=30s \
  --health-timeout=10s \
  --health-retries=3 \
  --health-start-period=40s \
  jeweis/mcpcat:latest
```

#### 使用 Docker Compose

```bash
# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

### 自行构建镜像

#### 开发版本

```bash
docker build -t mcpcat:dev .
```

#### 生产版本

```bash
docker build -f Dockerfile.production -t mcpcat:prod .
```

### 部署前准备

```bash
# 创建必要的目录
mkdir -p logs data

# 确保配置文件存在
# config.json 文件是必需的，包含 MCP 服务器配置
```

### 常用管理命令

```bash
# 查看容器状态
docker ps

# 查看容器日志
docker logs mcpcat-app -f

# 进入容器
docker exec -it mcpcat-app /bin/bash

# 停止容器
docker stop mcpcat-app

# 重启容器
docker restart mcpcat-app
```

### 首页展示
  ![image](https://github.com/user-attachments/assets/ad70c84d-ee00-48e6-b22f-986c9c0a5c1b)


## 项目结构

```
mcpcat/
├── main.py              # FastAPI应用入口
├── requirements.txt     # Python依赖
├── README.md           # 项目说明
├── .gitignore          # Git忽略文件
├── .env.example        # 环境变量示例
└── app/                # 应用代码目录
    ├── __init__.py
    ├── api/            # API路由
    ├── core/           # 核心功能
    ├── models/         # 数据模型
    └── services/       # 业务服务
```

## 开发计划

- [ ] MCP协议支持
- [ ] 服务管理界面
- [ ] 配置管理
- [ ] 监控和日志
- [x] 部署文档

## 相关资源

- [FastAPI官方文档](https://fastapi.tiangolo.com/)
- [Model Context Protocol](https://modelcontextprotocol.io/introduction)
- [FastMCP框架](https://gofastmcp.com/getting-started/welcome)

## 许可证

MIT License
