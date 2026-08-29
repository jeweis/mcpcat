<div align="center">
  <img src="assets/mcpcat-logo.png" alt="mcpcat" width="200"/>

  # mcpcat

  一个MCP（Model Context Protocol）聚合平台，支持多种MCP协议的统一管理和运行。
</div>

---

## 功能特性

- 🔌 支持多种MCP协议：
  - STDIO (默认，用于本地工具)
  - Streamable HTTP (推荐用于Web服务)
  - SSE (传统Web传输)
- 🧩支持openapi3配置，直接转为mcp协议
- 📊 统一的MCP服务管理界面

## 用户文档

- [mcpcat CLI：安装与使用](docs/user/cli.md)
- [文档索引](docs/README.md)

## Docker 部署（推荐）

> Catalog 工具搜索依赖进程内的动态 MCP 服务目录。当前版本请保持单容器、单 Uvicorn worker 部署；在引入共享服务 registry 前，不支持通过 `--workers` 或多副本横向扩容。

### 快速启动

最简单的启动方式，系统会自动生成 API Key：

```bash
docker run -d --name mcpcat -p 8000:8000 -v mcpcat_data:/app/.mcpcat --restart always jeweis/mcpcat:latest
```

#### 查看API Key的方式
1. 方式一：首次打开控制台网址时，会在页面上展示自动生成的 API Key
3. 方式二：首次启动时会在日志中显示，可通过该命令从日志中查看
```bash
docker logs mcpcat
```

### 自定义 API Key 启动

如果需要指定 API Key（推荐用于生产环境），可以通过环境变量配置：

```bash
docker run -d \
  --name mcpcat \
  -p 8000:8000 \
  -e MCPCAT_DEFAULT_ADMIN_KEY=your-secure-admin-key \
  -e MCPCAT_DEFAULT_READ_KEY=your-secure-read-key \
  -v mcpcat_data:/app/.mcpcat \
  --restart unless-stopped \
  jeweis/mcpcat:latest
```

**API Key 说明：**
- `MCPCAT_DEFAULT_ADMIN_KEY`：管理员密钥，拥有读写权限（添加/删除/重启服务器）
- `MCPCAT_DEFAULT_READ_KEY`：只读密钥，仅可查看服务器状态
- 访问 API 时需要在请求头中添加 `Mcpcat-Key: your-api-key`

### 访问服务

- 控制台页面: http://localhost:8000
- API文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/api/health
- Streamable HTTP 端点: http://localhost:8000/mcp/{服务器名称}
- SSE 端点: http://localhost:8000/sse/{服务器名称}

### 管理容器

```bash
# 查看日志
docker logs -f mcpcat

# 停止容器
docker stop mcpcat

# 启动容器
docker start mcpcat

# 重启容器
docker restart mcpcat

# 删除容器
docker rm -f mcpcat
```

### 配置文件管理

配置文件存储在 Docker 卷中，管理方法：

```bash
# 查看配置文件
docker exec mcpcat cat /app/.mcpcat/config.json

# 复制到本地编辑
docker cp mcpcat:/app/.mcpcat/config.json ./config.json
# 编辑后复制回去
docker cp ./config.json mcpcat:/app/.mcpcat/config.json
# 重启使配置生效
docker restart mcpcat
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

或使用 uv
```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
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
