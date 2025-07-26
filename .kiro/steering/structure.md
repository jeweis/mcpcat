# 项目结构与组织

## 根目录结构
```
mcpcat/
├── main.py                 # FastAPI 应用入口点
├── config.json            # MCP 服务器配置
├── requirements.txt       # Python 依赖
├── pyproject.toml         # 现代 Python 包配置
├── docker-compose.yml     # Docker Compose 配置
├── Dockerfile             # 开发环境 Docker 镜像
├── Dockerfile.production  # 生产环境 Docker 镜像
├── .env.example           # 环境变量模板
├── static/                # 静态 Web 资源
│   └── index.html         # 前端管理界面
├── logs/                  # 应用日志 (运行时创建)
├── data/                  # 持久化数据存储
└── app/                   # 主应用包
```

## 应用包结构 (`app/`)
```
app/
├── __init__.py            # 包初始化
├── api/                   # API 路由处理器
│   ├── __init__.py
│   ├── health.py          # 健康检查端点
│   └── servers.py         # 服务器管理端点
├── core/                  # 核心应用逻辑
│   ├── __init__.py
│   └── config.py          # 应用设置与配置
├── models/                # 数据模型和模式
│   ├── __init__.py
│   └── mcp_config.py      # MCP 服务器配置模型
└── services/              # 业务逻辑服务
    ├── __init__.py
    ├── config_service.py  # 配置管理
    ├── mcp_factory.py     # MCP 服务器工厂
    └── server_manager.py  # 服务器生命周期管理
```

## 架构模式

### 模块化服务架构
- **服务层**: 业务逻辑封装在服务类中
- **API 层**: 用于 HTTP 端点的 FastAPI 路由器
- **模型层**: 用于数据验证的 Pydantic 模型
- **核心层**: 应用配置和共享工具

### 关键组件
- **MCPServerManager**: 管理 MCP 服务器生命周期的中央服务
- **ConfigService**: 处理配置加载和验证
- **MCPServerFactory**: 根据类型创建 MCP 服务器实例
- **Health & Server APIs**: 用于监控和管理的 RESTful 端点

### 配置管理
- **config.json**: 主要的 MCP 服务器定义，支持多种传输类型
- **Settings**: 基于 Pydantic 的配置，支持环境变量覆盖
- **环境变量**: 可选的 `.env` 文件用于部署特定设置

## 文件命名约定
- **snake_case**: Python 文件和模块
- **PascalCase**: 类名
- **lowercase**: 包目录
- **kebab-case**: Docker 和配置文件

## 导入组织
- 标准库导入在前
- 第三方库导入在中
- 本地应用导入在后
- 使用从 `app.` 包根的绝对导入

## 部署结构
- **Docker**: 多阶段构建，包含开发和生产版本
- **卷挂载**: 配置、日志和数据目录
- **健康检查**: 内置容器健康监控
- **网络**: 用于服务通信的隔离 Docker 网络