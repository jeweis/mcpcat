#!/bin/bash

# mcpcat Docker 部署脚本

set -e

echo "🚀 开始部署 mcpcat..."

# 检查 Docker 是否安装
if ! command -v docker &> /dev/null; then
    echo "❌ Docker 未安装，请先安装 Docker"
    exit 1
fi

# 检查 Docker Compose 是否安装
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose 未安装，请先安装 Docker Compose"
    exit 1
fi

# 检查配置文件是否存在
echo "📋 检查配置文件..."
if [ ! -f ".mcpcat/config.json" ]; then
    echo "⚠️ 配置文件 .mcpcat/config.json 不存在"
    echo "正在创建默认配置文件..."
    mkdir -p .mcpcat
    cat > .mcpcat/config.json << 'EOF'
{
  "mcpServers": {},
  "security": {
    "api_keys": [],
    "auth_header_name": "Mcpcat-Key"
  },
  "app": {
    "version": "0.1.1",
    "log_level": "INFO",
    "enable_metrics": true
  }
}
EOF
    echo "✅ 已创建默认配置文件 .mcpcat/config.json"
    echo "💡 提示：您可以编辑 .mcpcat/config.json 文件来配置 MCP 服务器"
else
    echo "✅ 配置文件 .mcpcat/config.json 已存在"
fi

# 停止现有容器（如果存在）
echo "🛑 停止现有容器..."
docker-compose down 2>/dev/null || true

# 构建镜像
echo "🔨 构建 Docker 镜像..."
docker-compose build --no-cache

# 启动服务
echo "▶️ 启动服务..."
docker-compose up -d

# 等待服务启动
echo "⏳ 等待服务启动..."
sleep 10

# 检查服务状态
echo "🔍 检查服务状态..."
if docker-compose ps | grep -q "Up"; then
    echo "✅ mcpcat 部署成功！"
    echo "📱 访问地址: http://localhost:8000"
    echo "🏥 健康检查: http://localhost:8000/api/health"
    echo ""
    echo "📋 常用命令:"
    echo "  查看日志: docker-compose logs -f"
    echo "  停止服务: docker-compose down"
    echo "  重启服务: docker-compose restart"
    echo "  查看状态: docker-compose ps"
else
    echo "❌ 部署失败，请检查日志:"
    docker-compose logs
    exit 1
fi