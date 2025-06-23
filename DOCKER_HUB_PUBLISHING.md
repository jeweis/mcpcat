# Docker Hub 官方镜像发布指南

本指南将帮助您将 MCPCat 项目发布到 Docker Hub 官方镜像库。

## 前置准备

### 1. Docker Hub 账户
- 注册 [Docker Hub](https://hub.docker.com/) 账户
- 验证邮箱地址
- 创建访问令牌（推荐使用令牌而非密码）

### 2. 本地环境
```bash
# 安装 Docker
# 登录 Docker Hub
docker login
```

## 镜像命名规范

### 标准命名格式
```
<username>/<repository>:<tag>
# 例如：
jeweis/mcpcat:latest
jeweis/mcpcat:v1.0.0
jeweis/mcpcat:1.0.0-python3.13
```

### 推荐的标签策略
- `latest` - 最新稳定版本
- `v1.0.0` - 具体版本号
- `1.0.0-python3.13` - 版本号 + Python版本
- `dev` - 开发版本
- `stable` - 稳定版本

## 构建和发布流程

### 1. 修改 Dockerfile（多阶段构建优化）

创建优化的生产环境 Dockerfile：

```dockerfile
# 多阶段构建 - 构建阶段
FROM python:3.12-slim-bookworm as builder

WORKDIR /app

# 安装构建依赖
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖到临时目录
RUN pip install --user --no-cache-dir -r requirements.txt

# 生产阶段
FROM python:3.12-slim-bookworm

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装运行时依赖
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# 创建非 root 用户
RUN groupadd -r mcpcat && useradd -r -g mcpcat mcpcat

# 设置工作目录
WORKDIR /app

# 从构建阶段复制 Python 包
COPY --from=builder /root/.local /home/mcpcat/.local

# 复制应用代码
COPY --chown=mcpcat:mcpcat . .

# 确保 Python 包路径正确
ENV PATH=/home/mcpcat/.local/bin:$PATH

# 切换到非 root 用户
USER mcpcat

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令
CMD ["python", "main.py"]
```

### 2. 构建多架构镜像

#### 设置 buildx
```bash
# 创建新的构建器实例
docker buildx create --name multiarch --driver docker-container --use
docker buildx inspect --bootstrap
```

#### 构建多架构镜像
```bash
# 构建并推送多架构镜像
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7 \
  --tag jeweis/mcpcat:latest \
  --tag jeweis/mcpcat:v1.0.0 \
  --push .
```

### 3. 手动发布流程

```bash
# 1. 构建镜像
docker build -t jeweis/mcpcat:latest .
docker build -t jeweis/mcpcat:v1.0.0 .

# 2. 测试镜像
docker run --rm -p 8000:8000 jeweis/mcpcat:latest

# 3. 推送到 Docker Hub
docker push jeweis/mcpcat:latest
docker push jeweis/mcpcat:v1.0.0
```

## 自动化 CI/CD

### GitHub Actions 配置

创建 `.github/workflows/docker-publish.yml`：

```yaml
name: Docker Build and Push

on:
  push:
    branches: [ main ]
    tags: [ 'v*' ]
  pull_request:
    branches: [ main ]

env:
  REGISTRY: docker.io
  IMAGE_NAME: jeweis/mcpcat

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
    - name: Checkout repository
      uses: actions/checkout@v4

    - name: Set up Docker Buildx
      uses: docker/setup-buildx-action@v3

    - name: Log in to Docker Hub
      if: github.event_name != 'pull_request'
      uses: docker/login-action@v3
      with:
        registry: ${{ env.REGISTRY }}
        username: ${{ secrets.DOCKER_USERNAME }}
        password: ${{ secrets.DOCKER_PASSWORD }}

    - name: Extract metadata
      id: meta
      uses: docker/metadata-action@v5
      with:
        images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
        tags: |
          type=ref,event=branch
          type=ref,event=pr
          type=semver,pattern={{version}}
          type=semver,pattern={{major}}.{{minor}}
          type=semver,pattern={{major}}
          type=raw,value=latest,enable={{is_default_branch}}

    - name: Build and push Docker image
      uses: docker/build-push-action@v5
      with:
        context: .
        platforms: linux/amd64,linux/arm64
        push: ${{ github.event_name != 'pull_request' }}
        tags: ${{ steps.meta.outputs.tags }}
        labels: ${{ steps.meta.outputs.labels }}
        cache-from: type=gha
        cache-to: type=gha,mode=max
```

### 配置 GitHub Secrets

在 GitHub 仓库设置中添加以下 Secrets：
- `DOCKER_USERNAME`: Docker Hub 用户名
- `DOCKER_PASSWORD`: Docker Hub 访问令牌

## 镜像优化

### 1. 减小镜像大小

```dockerfile
# 使用 slim 基础镜像
FROM python:3.12-slim-bookworm

# 合并 RUN 命令
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# 使用 .dockerignore
# 多阶段构建
# 删除不必要的文件
```

### 2. 安全优化

```dockerfile
# 使用非 root 用户
USER mcpcat

# 设置只读文件系统（可选）
# 扫描漏洞
docker scout cves jeweis/mcpcat:latest
```

### 3. 性能优化

```dockerfile
# 启用 Python 优化
ENV PYTHONOPTIMIZE=1

# 预编译 Python 文件
RUN python -m compileall .

# 使用缓存
```

## 发布检查清单

### 发布前检查
- [ ] 代码已合并到主分支
- [ ] 版本号已更新
- [ ] CHANGELOG 已更新
- [ ] 文档已更新
- [ ] 测试通过
- [ ] 安全扫描通过

### 镜像质量检查
- [ ] 镜像大小合理（< 500MB）
- [ ] 支持多架构
- [ ] 健康检查正常
- [ ] 安全扫描无高危漏洞
- [ ] 启动时间合理（< 30s）

### 文档检查
- [ ] README 包含使用说明
- [ ] Docker Hub 描述完整
- [ ] 标签说明清晰
- [ ] 示例代码正确

## Docker Hub 仓库设置

### 1. 仓库描述
```
MCPCat - A powerful MCP (Model Context Protocol) server management tool

Features:
- Easy MCP server configuration
- RESTful API
- Web-based management interface
- Docker support

Quick start:
docker run -p 8000:8000 jeweis/mcpcat:latest
```

### 2. README 内容
- 项目简介
- 快速开始
- 环境变量说明
- 数据持久化
- 网络配置
- 故障排除

### 3. 自动构建
- 连接 GitHub 仓库
- 设置构建规则
- 配置 Webhook

## 维护和更新

### 版本管理
```bash
# 发布新版本
git tag v1.1.0
git push origin v1.1.0

# 自动触发 CI/CD 构建
```

### 监控和维护
- 定期更新基础镜像
- 监控安全漏洞
- 检查镜像使用情况
- 清理旧版本镜像

### 社区支持
- 及时回复 Issues
- 更新文档
- 提供使用示例
- 收集用户反馈

## 最佳实践

1. **语义化版本控制**: 使用 semver 规范
2. **安全第一**: 定期扫描和更新
3. **文档完善**: 保持文档同步更新
4. **自动化**: 使用 CI/CD 自动化发布
5. **监控**: 监控镜像使用和性能
6. **社区**: 积极参与社区互动

## 故障排除

### 常见问题

1. **推送失败**
   ```bash
   # 检查登录状态
   docker info
   
   # 重新登录
   docker logout
   docker login
   ```

2. **多架构构建失败**
   ```bash
   # 检查 buildx
   docker buildx ls
   
   # 重新创建构建器
   docker buildx rm multiarch
   docker buildx create --name multiarch --use
   ```

3. **镜像过大**
   - 使用多阶段构建
   - 优化 .dockerignore
   - 清理缓存和临时文件

通过遵循这个指南，您可以成功将 MCPCat 项目发布到 Docker Hub，并建立一个专业的容器化分发流程。