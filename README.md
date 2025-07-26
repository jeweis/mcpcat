# mcapcat

一个MCP（Model Context Protocol）聚合平台，支持多种MCP协议的统一管理和运行。

## 功能特性

- 🔌 支持多种MCP协议：
  - STDIO (默认，用于本地工具)
  - Streamable HTTP (推荐用于Web服务)
  - SSE (传统Web传输)
- 🧩支持openapi3配置，直接转为mcp协议
- 📊 统一的MCP服务管理界面

## 快速开始

## Docker 部署（推荐）


#### 快速启动

```bash
docker run -d \
  --name mcpcat-app \
  -p 8000:8000 \
  -v $(pwd)/config.json:/home/app/config.json:ro \
  -v $(pwd)/logs:/app/logs \
  --restart unless-stopped \
  jeweis/mcpcat:latest
```


### 访问服务

- 控制台页面: http://localhost:8000
- API文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/health
- streamable http访问端点：http://{本机ip}:8000/sse/{Mcp Server名称}
- sse访问端点：http://{本机ip}:8000/mcp/{Mcp Server名称}

#### 完整配置启动

```bash
docker run -d \
  --name mcpcat-app \
  -p 8000:8000 \
  -e APP_NAME=mcpcat \
  -e APP_VERSION=0.1.1 \
  -e HOST=0.0.0.0 \
  -e PORT=8000 \
  -e LOG_LEVEL=INFO \
  -e MCPCAT_CONFIG_PATH=/home/app/config.json \
  -v $(pwd)/config.json:/home/app/config.json:ro \
  -v $(pwd)/logs:/home/app/logs \
  -v $(pwd)/data:/home/app/data \
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


## 源码运行
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


## 许可证

MIT License
