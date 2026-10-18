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
