# mcpcat Docker 使用指南

## 🚀 快速开始

### 使用预构建镜像

```bash
# 拉取最新镜像
docker pull jeweis/mcpcat:latest

# 快速启动 (使用默认配置)
docker run -d \
  --name mcpcat \
  -p 8000:8000 \
  jeweis/mcpcat:latest

# 使用自定义配置启动

**Linux/Mac**:
```bash
docker run -d \
  --name mcpcat \
  -p 8000:8000 \
  -v $(pwd)/.mcpcat:/app/.mcpcat \
  jeweis/mcpcat:latest
```

**Windows PowerShell**:
```powershell
docker run -d `
  --name mcpcat `
  -p 8000:8000 `
  -v ${PWD}/.mcpcat:/app/.mcpcat `
  jeweis/mcpcat:latest
```

**Windows CMD**:
```cmd
docker run -d ^
  --name mcpcat ^
  -p 8000:8000 ^
  -v %cd%/.mcpcat:/app/.mcpcat ^
  jeweis/mcpcat:latest
```
```

### 使用 Docker Compose

```yaml
version: '3.8'

services:
  mcpcat:
    image: jeweis/mcpcat:latest
    container_name: mcpcat
    ports:
      - "8000:8000"
    volumes:
      - ./.mcpcat:/app/.mcpcat
    environment:
      - APP_NAME=mcpcat
      - LOG_LEVEL=INFO
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

## 🔧 环境变量

| 变量名 | 默认值 | 描述 |
|--------|--------|------|
| `APP_NAME` | mcpcat | 应用名称 |
| `APP_VERSION` | 0.1.1 | 应用版本 |
| `HOST` | 0.0.0.0 | 监听地址 |
| `PORT` | 8000 | 监听端口 |
| `LOG_LEVEL` | INFO | 日志级别 |
| `MCPCAT_CONFIG_PATH` | .mcpcat/config.json | 配置文件路径 |

## 📁 卷挂载

| 容器路径 | 描述 | 建议挂载 |
|----------|------|----------|
| `/app/.mcpcat/config.json` | MCP服务器配置文件 | 必需 |

## 🏥 健康检查

容器内置健康检查，检查端点：`http://localhost:8000/api/health`

```bash
# 检查容器健康状态
docker ps --format "table {{.Names}}\t{{.Status}}"

# 查看健康检查日志
docker inspect mcpcat --format='{{json .State.Health}}'
```

## 📊 监控和日志

```bash
# 查看实时日志（推荐方式）
docker logs -f mcpcat

# 使用docker-compose查看日志
docker-compose logs -f

# 查看容器资源使用
docker stats mcpcat

# 进入容器调试
docker exec -it mcpcat /bin/bash
```

**注意**：MCPCat 的日志输出到标准输出流，通过 `docker logs` 命令查看，不会写入文件。

## 🔄 更新和维护

```bash
# 更新到最新版本
docker pull jeweis/mcpcat:latest
docker stop mcpcat
docker rm mcpcat
docker run -d --name mcpcat -p 8000:8000 -v $(pwd)/.mcpcat:/app/.mcpcat jeweis/mcpcat:latest

# 备份配置
docker cp mcpcat:/app/.mcpcat/config.json ./config-backup.json

# 清理旧镜像
docker image prune -f
```

## 🐛 故障排除

### 常见问题

1. **容器启动失败**
   ```bash
   # 查看详细错误信息
   docker logs mcpcat
   
   # 检查配置文件格式
   docker run --rm -v $(pwd)/.mcpcat:/app/.mcpcat jeweis/mcpcat:latest python -c "import json; json.load(open('/app/.mcpcat/config.json'))"
   ```

2. **端口冲突**
   ```bash
   # 使用不同端口
   docker run -d --name mcpcat -p 8080:8000 jeweis/mcpcat:latest
   ```

3. **权限问题**
   ```bash
   # 检查文件权限
   ls -la .mcpcat/config.json
   
   # 修复权限
   chmod 644 .mcpcat/config.json
   ```

### 调试模式

```bash
# 以调试模式启动

**Linux/Mac**:
```bash
docker run -it --rm \
  -p 8000:8000 \
  -v $(pwd)/.mcpcat:/app/.mcpcat \
  -e LOG_LEVEL=DEBUG \
  jeweis/mcpcat:latest
```

**Windows PowerShell**:
```powershell
docker run -it --rm `
  -p 8000:8000 `
  -v ${PWD}/.mcpcat:/app/.mcpcat `
  -e LOG_LEVEL=DEBUG `
  jeweis/mcpcat:latest
```

**Windows CMD**:
```cmd
docker run -it --rm ^
  -p 8000:8000 ^
  -v %cd%/.mcpcat:/app/.mcpcat ^
  -e LOG_LEVEL=DEBUG ^
  jeweis/mcpcat:latest
```
```

## 🔗 相关链接

- [Docker Hub](https://hub.docker.com/r/jeweis/mcpcat)
- [GitHub Repository](https://github.com/your-username/mcpcat)
- [项目文档](README.md)