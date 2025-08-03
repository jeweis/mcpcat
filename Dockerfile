# 使用官方 Python 3.12 slim 镜像作为基础镜像
FROM python:3.12-slim-bookworm

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NODE_VERSION=20.x

# 安装系统依赖和 Node.js
RUN apt-get update && \
    apt-get install -y --fix-missing --no-install-recommends \
    gcc \
    curl \
    gnupg \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_VERSION nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# 验证 Node.js 和 npm 安装
RUN node --version && npm --version

# 安装 uv 和 uvx（用于运行 Python MCP 服务器）
RUN pip install --no-cache-dir uv

# 复制依赖文件
COPY requirements.txt .
COPY pyproject.toml .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建非 root 用户 (统一使用UID=1000)
RUN groupadd -r app --gid=1000 && \
    useradd -r -g app --uid=1000 --home-dir=/home/app --create-home app \
    && chown -R app:app /app

# 创建配置目录并设置权限
RUN mkdir -p /app/.mcpcat && chown -R app:app /app/.mcpcat

# 确保用户可以访问全局 npm 包
RUN mkdir -p /home/app/.npm-global \
    && chown -R app:app /home/app/.npm-global

USER app

# 设置用户级的 npm 配置
ENV PATH="/home/app/.npm-global/bin:$PATH"
RUN npm config set prefix '/home/app/.npm-global'

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# 启动命令
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]