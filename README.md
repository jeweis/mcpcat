# mcpcat

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
docker run -d --name mcpcat -p 8000:8000 -v mcpcat_data:/app/.mcpcat --restart unless-stopped jeweis/mcpcat:latest
```


### 访问服务

- 控制台页面: http://localhost:8000
- API文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/api/health
- Streamable HTTP访问端点：http://{本机ip}:8000/mcp/{Mcp Server名称}
- SSE访问端点：http://{本机ip}:8000/sse/{Mcp Server名称}

#### 完整配置启动

**完整配置启动**：
```bash
docker run -d \
  --name mcpcat \
  -p 8000:8000 \
  -e APP_NAME=mcpcat \
  -e LOG_LEVEL=INFO \
  -v mcpcat_data:/app/.mcpcat \
  --restart unless-stopped \
  --health-cmd="curl -f http://localhost:8000/api/health" \
  --health-interval=30s \
  --health-timeout=10s \
  --health-retries=3 \
  jeweis/mcpcat:latest
```

### 配置文件管理

由于使用了Docker命名卷，配置文件存储在Docker管理的卷中。管理配置文件的方法：

```bash
# 查看配置文件
docker exec mcpcat cat /app/.mcpcat/config.json

# 编辑配置文件
docker exec -it mcpcat vi /app/.mcpcat/config.json

# 复制配置文件到本地编辑
docker cp mcpcat:/app/.mcpcat/config.json ./config.json
# 编辑后复制回去
docker cp ./config.json mcpcat:/app/.mcpcat/config.json
# 重启容器使配置生效
docker restart mcpcat
```

### 管理服务

```bash
# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down

# 重启服务
docker-compose restart
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
├── main.py                 # FastAPI应用入口
├── requirements.txt        # Python依赖
├── config.example.json     # 配置文件示例
├── pyproject.toml          # Python包配置
├── docker-compose.yml      # Docker Compose配置
├── Dockerfile              # Docker镜像配置
├── Dockerfile.production   # 生产环境Docker镜像
├── .env.example            # 环境变量模板
├── .mcpcat/                # 配置目录
│   └── config.json         # MCP服务器配置文件
├── static/                 # 静态Web资源
│   └── index.html          # 前端管理界面
└── app/                    # 应用代码目录
    ├── __init__.py
    ├── api/                # API路由
    │   ├── auth.py         # 认证端点
    │   ├── health.py       # 健康检查端点
    │   └── servers.py      # 服务器管理端点
    ├── core/               # 核心功能
    │   └── config.py       # 应用配置
    ├── middleware/         # 中间件
    │   └── auth.py         # 认证中间件
    ├── models/             # 数据模型
    │   └── mcp_config.py   # MCP配置模型
    ├── services/           # 业务服务
    │   ├── config_service.py    # 配置服务
    │   ├── mcp_factory.py       # MCP工厂
    │   ├── security_service.py  # 安全服务
    │   └── server_manager.py    # 服务器管理器
    └── exceptions/         # 自定义异常
        └── auth.py         # 认证异常
```

## 开发计划

- [x] MCP协议支持
- [x] 服务管理界面
- [x] 配置管理
- [x] 监控和日志
- [x] 部署文档
- [x] API Key认证
- [x] 动态服务器管理


## 许可证

MIT License
