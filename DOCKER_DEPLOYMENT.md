# MCPCat Docker 部署指南

本指南将帮助您使用 Docker 部署 MCPCat 应用。

## 前置要求

- Docker Engine 20.10+
- Docker Compose 2.0+
- 至少 512MB 可用内存
- 端口 8000 可用

## 配置文件准备

在启动容器之前，请确保准备好配置文件：

### 1. 检查配置文件
```bash
# 检查当前目录是否存在 config.json
ls config.json
```

### 2. 如果没有配置文件，创建一个
```bash
# 复制示例配置文件（如果存在）
cp config.json.example config.json

# 或者创建基本配置文件
cat > config.json << EOF
{
  "servers": [],
  "settings": {
    "auto_start": false,
    "log_level": "INFO"
  }
}
EOF
```

### 3. 编辑配置文件
```bash
# 使用你喜欢的编辑器编辑配置
nano config.json
# 或
vim config.json
```

## 快速开始

### 方法一：使用部署脚本（推荐）

**Linux/macOS:**
```bash
# 给脚本执行权限
chmod +x deploy.sh

# 运行部署脚本
./deploy.sh
```

**Windows:**
```cmd
# 双击运行或在命令行执行
deploy.bat
```

### 方法二：手动部署

1. **构建镜像**
   ```bash
   docker-compose build
   ```

2. **启动服务**
   ```bash
   docker-compose up -d
   ```

3. **验证部署**
   ```bash
   # 检查容器状态
   docker-compose ps
   
   # 查看日志
   docker-compose logs -f
   ```

## 访问应用

- **Web 界面**: http://localhost:8000
- **API 文档**: http://localhost:8000/docs
- **健康检查**: http://localhost:8000/api/health

## 配置说明

### 环境变量

可以通过修改 `docker-compose.yml` 中的环境变量来配置应用：

```yaml
environment:
  - APP_NAME=MCPCat                    # 应用名称
  - APP_VERSION=0.1.1                 # 应用版本
  - HOST=0.0.0.0                      # 监听地址
  - PORT=8000                         # 监听端口
  - LOG_LEVEL=INFO                    # 日志级别
  - MCPCAT_CONFIG_PATH=/app/config.json # 配置文件路径
```

### 数据持久化

默认配置会挂载以下目录：

- `./config.json:/app/config.json:ro` - **配置文件（默认从宿主机挂载，只读）**
- `./logs:/app/logs` - 日志目录
- `./data:/app/data` - 数据目录（可选）

**重要说明**：
- `config.json` 文件会默认从宿主机当前目录挂载到容器内
- 请确保宿主机上存在 `config.json` 文件，否则容器启动可能失败
- 如果没有配置文件，可以复制 `config.json` 示例文件进行修改

## 常用命令

### 服务管理

```bash
# 启动服务
docker-compose up -d

# 停止服务
docker-compose down

# 重启服务
docker-compose restart

# 查看状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 进入容器
docker-compose exec mcpcat bash
```

### 镜像管理

```bash
# 重新构建镜像
docker-compose build --no-cache

# 拉取最新镜像
docker-compose pull

# 清理未使用的镜像
docker image prune
```

## 生产环境部署

### 1. 创建生产环境配置

创建 `docker-compose.prod.yml`：

```yaml
version: '3.8'

services:
  mcpcat:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: mcpcat-prod
    ports:
      - "80:8000"  # 生产环境通常使用 80 端口
    environment:
      - APP_NAME=MCPCat
      - LOG_LEVEL=WARNING  # 生产环境减少日志输出
    volumes:
      - ./config.json:/app/config.json:ro
      - mcpcat-logs:/app/logs
    restart: always  # 生产环境自动重启
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: '0.5'
        reservations:
          memory: 256M
          cpus: '0.25'

volumes:
  mcpcat-logs:
    driver: local
```

### 2. 使用生产配置部署

```bash
docker-compose -f docker-compose.prod.yml up -d
```

### 3. 配置反向代理（可选）

如果需要 HTTPS 或域名访问，可以配置 Nginx 反向代理：

```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 故障排除

### 常见问题

1. **端口被占用**
   ```bash
   # 检查端口占用
   netstat -tulpn | grep 8000
   
   # 修改 docker-compose.yml 中的端口映射
   ports:
     - "8001:8000"  # 使用其他端口
   ```

2. **容器启动失败**
   ```bash
   # 查看详细日志
   docker-compose logs mcpcat
   
   # 检查容器状态
   docker-compose ps
   ```

3. **健康检查失败**
   ```bash
   # 手动测试健康检查
   curl http://localhost:8000/api/health
   
   # 进入容器检查
   docker-compose exec mcpcat bash
   ```

### 日志分析

```bash
# 查看实时日志
docker-compose logs -f mcpcat

# 查看最近 100 行日志
docker-compose logs --tail=100 mcpcat

# 查看特定时间的日志
docker-compose logs --since="2024-01-01T00:00:00" mcpcat
```

## 安全建议

1. **不要在生产环境中暴露调试端口**
2. **定期更新基础镜像**
3. **使用非 root 用户运行容器**（已在 Dockerfile 中配置）
4. **限制容器资源使用**
5. **配置适当的网络安全组规则**

## 监控和维护

### 健康检查

应用内置了健康检查端点：

```bash
# 检查应用健康状态
curl http://localhost:8000/api/health
```

### 资源监控

```bash
# 查看容器资源使用情况
docker stats mcpcat-app

# 查看容器详细信息
docker inspect mcpcat-app
```

### 备份和恢复

```bash
# 备份配置文件
cp config.json config.json.backup

# 备份日志
tar -czf logs-backup-$(date +%Y%m%d).tar.gz logs/
```

## 更新应用

```bash
# 1. 停止当前服务
docker-compose down

# 2. 拉取最新代码
git pull

# 3. 重新构建镜像
docker-compose build --no-cache

# 4. 启动新版本
docker-compose up -d
```