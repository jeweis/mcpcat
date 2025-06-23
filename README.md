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
- [ ] 部署文档

## 相关资源

- [FastAPI官方文档](https://fastapi.tiangolo.com/)
- [Model Context Protocol](https://modelcontextprotocol.io/introduction)
- [FastMCP框架](https://gofastmcp.com/getting-started/welcome)

## 许可证

MIT License
