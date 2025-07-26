# 技术栈与构建系统

## 核心技术
- **Python**: 3.10+ (主要编程语言)
- **FastAPI**: 用于 REST API 和异步操作的 Web 框架
- **Uvicorn**: 生产环境 ASGI 服务器
- **Pydantic**: 数据验证和设置管理
- **FastMCP**: MCP 协议实现框架

## 主要依赖
- `fastapi>=0.115.0` - Web 框架
- `uvicorn[standard]>=0.32.0` - ASGI 服务器
- `pydantic>=2.10.0` - 数据验证
- `fastmcp>=2.8.1` - MCP 协议支持
- `httpx>=0.24.0` - 外部服务 HTTP 客户端
- `aiofiles>=24.1.0` - 异步文件操作
- `python-dotenv>=1.0.1` - 环境变量管理

## 开发工具
- **Black**: 代码格式化 (行长度: 88)
- **isort**: 导入排序 (black 配置)
- **pytest**: 支持异步的测试框架
- **flake8**: 代码检查

## 构建与包管理
- **pyproject.toml**: 现代 Python 包配置
- **requirements.txt**: 生产环境依赖
- 包名: `jewei-mcpcat`
- 构建系统: `hatchling`

## 常用命令

### 开发环境
```bash
# 安装依赖
pip install -r requirements.txt

# 运行开发服务器
python main.py
# 或者
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 运行测试
pytest

# 格式化代码
black .
isort .

# 代码检查
flake8
```

### Docker 部署
```bash
# 构建开发镜像
docker build -t mcpcat:dev .

# 构建生产镜像
docker build -f Dockerfile.production -t mcpcat:prod .

# 使用 Docker Compose 运行
docker-compose up -d

# 查看日志
docker-compose logs -f
```

### 配置管理
- 主配置: `config.json` (MCP 服务器定义)
- 环境变量: `.env` 文件 (可选，参考 `.env.example`)
- 通过 Pydantic BaseSettings 管理设置，支持环境变量覆盖

## 部署方式
- **Docker**: 主要部署方式，支持多阶段构建
- **健康检查**: 内置健康检查端点 `/api/health`
- **日志**: 结构化日志，可配置日志级别
- **端口**: 默认 8000，可通过环境变量配置