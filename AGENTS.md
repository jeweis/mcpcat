# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此代码仓库中工作时提供指导。

**请始终使用中文输出。**

## 构建和运行命令

```bash
# 安装依赖
pip install -r requirements.txt

# 运行开发服务器
python main.py
# 或启用热重载
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 运行测试
pytest

# 代码格式化
black .
isort .

# 代码检查
flake8

# Docker
docker build -t mcpcat:dev .
docker-compose up -d
```

## 架构概述

mcpcat 是一个 MCP（Model Context Protocol）聚合平台，通过统一的 FastAPI 接口管理多个 MCP 服务器。

### 核心流程（main.py）
1. **模块加载时**：创建全局 `MCPServerManager` 实例
2. **配置加载**：调用 `load_config()` → `ConfigService.load_mcp_servers_config()`
3. **安全初始化**：`security_service.ensure_default_keys()` 首次启动时自动创建默认 API Key
4. **服务器加载**：`server_manager.load_servers_from_config()` 遍历配置，通过 `MCPServerFactory` 创建 FastMCP 实例
5. **应用创建**：创建 FastAPI 应用，添加 `AuthMiddleware`，挂载所有服务器
6. **生命周期管理**：`lifespan_manager` 调用 `server_manager.create_unified_lifespan()` 统一管理所有 MCP 生命周期

### 核心组件

**MCPServerManager** (`app/services/server_manager.py`)
- MCP 服务器生命周期的中央调度器
- `add_mcp_server()`: 创建 FastMCP 实例、生成 mcp_app/sse_app、创建 `MCPProxyApp` 代理
- `mount_all_servers()`: 将代理应用挂载到 FastAPI
- `create_unified_lifespan()`: 使用 `AsyncExitStack` 管理所有 MCP 生命周期
- 维护 `server_info` 字典：存储 config、mcp、mcp_app、sse_app、status

**MCPProxyApp** (`app/services/server_manager.py`)
- ASGI 代理应用，包装实际的 MCP 应用
- 根据 `transport_type`（'mcp' 或 'sse'）转发请求到对应的应用实例
- 实现运行时实例切换，无需重启应用

**MCPServerFactory** (`app/services/mcp_factory.py`)
- 根据 `type` 字段创建 FastMCP 实例：
  - `stdio`: `FastMCP.as_proxy()` 配置 command/args/env
  - `sse`: `FastMCP.as_proxy()` 配置 url/transport="sse"/headers
  - `streamable-http`: `FastMCP.as_proxy()` 配置 url/transport="streamable-http"/headers
  - `openapi`: `FastMCP.from_openapi()` 配置 spec_url/api_base_url/route_configs

**ConfigService** (`app/services/config_service.py`)
- 配置文件路径：`settings.mcpcat_config_path`（默认 `.mcpcat/config.json`）
- `load_raw_config()`: 返回完整配置（包含 mcpServers、security、app）
- `load_mcp_servers_config()`: 只返回 mcpServers 部分
- `add_server_to_config()` / `update_server_config()` / `remove_server_from_config()`: 配置 CRUD

**SecurityService** (`app/services/security_service.py`)
- `verify_api_key()`: 验证 Key 有效性、启用状态、过期时间
- `has_permission()`: 检查权限（WRITE 包含 READ）
- `ensure_default_keys()`: 无 Key 时创建 "Default Admin Key"(write) 和 "Default Read Key"(read)
- `get_auth_header_name()`: 获取认证头名称（默认 `Mcpcat-Key`）

**AuthMiddleware** (`app/middleware/auth.py`)
- 公开路径白名单（正则匹配）：`^/$`、`^/static/.*`、`^/api/health$`、`^/api/auth/verify$`、`^/api/auth/config$`
- `is_mcp_server_public()`: 检查 MCP 服务器的 `require_auth` 配置
- `get_required_permission()`: 根据路径和 HTTP 方法判断所需权限

### API 端点

**健康检查** (`app/api/health.py`)
- `GET /api/health` - 返回 `{"message": "OK"}`
- `GET /api/status` - 返回 app_name、version、description、status

**认证** (`app/api/auth.py`)
- `POST /api/auth/verify` - 验证 API Key（公开，通过 Header 传递 Key）
- `GET /api/auth/info` - 获取当前用户信息（需认证）
- `GET /api/auth/config` - 获取 auth_header_name（公开）

**服务器管理** (`app/api/servers.py`)
- `GET /api/servers` - 列出所有服务器（READ）
- `GET /api/servers/{name}` - 获取服务器详情（READ）
- `GET /api/servers/{name}/health` - 服务器健康状态（READ）
- `GET /api/servers/{name}/config` - 获取服务器配置（READ）
- `POST /api/servers` - 添加服务器，调用 `add_and_mount_server()`（WRITE）
- `PUT /api/servers/{name}` - 更新配置并重启，调用 `restart_server()`（WRITE）
- `POST /api/servers/{name}/start` - 启动服务器（WRITE）
- `POST /api/servers/{name}/stop` - 停止服务器（WRITE）
- `POST /api/servers/{name}/restart` - 重启服务器（WRITE）
- `DELETE /api/servers/{name}` - 删除服务器，调用 `remove_server()`（WRITE）

**MCP 协议端点**
- `/mcp/{server_name}/*` - 使用 `mcp.http_app(path='/')` 创建（默认 Streamable HTTP 传输）
- `/sse/{server_name}/*` - 使用 `mcp.http_app(path="/", transport="sse")` 创建（SSE 传输）

## 配置

配置文件：`.mcpcat/config.json`（可通过环境变量 `MCPCAT_CONFIG_PATH` 覆盖）

```json
{
  "mcpServers": {
    "stdio-server": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-server-fetch"],
      "env": {},
      "require_auth": false,
      "timeout": 30
    },
    "sse-server": {
      "type": "sse",
      "url": "http://localhost:3001/sse",
      "headers": {"Authorization": "Bearer token"},
      "require_auth": true
    },
    "http-server": {
      "type": "streamable-http",
      "url": "http://localhost:3002/mcp",
      "headers": {}
    },
    "openapi-server": {
      "type": "openapi",
      "spec_url": "https://api.example.com/openapi.json",
      "api_base_url": "https://api.example.com",
      "route_configs": [
        {"methods": ["GET", "POST"], "pattern": "^/api/.*"}
      ]
    }
  },
  "security": {
    "api_keys": [
      {"key": "...", "name": "Admin", "permission": "write", "enabled": true}
    ],
    "auth_header_name": "Mcpcat-Key"
  },
  "app": {
    "version": "0.1.1",
    "log_level": "INFO"
  }
}
```

## 关键设计模式

- **代理模式**：`MCPProxyApp` 是 ASGI 应用，根据 `server_info` 动态转发请求，支持运行时实例切换
- **动态服务器管理**：`add_and_mount_server()` 在运行时添加服务器，创建独立 `asyncio.Task` 管理生命周期
- **统一生命周期**：启动时的服务器通过 `AsyncExitStack` 管理，动态添加的服务器存储在 `dynamic_tasks` 集合中
