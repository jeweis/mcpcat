#!/bin/bash

# MCPCat Docker 构建和发布脚本
# 使用方法: ./build-and-push.sh [your-dockerhub-username]

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 获取版本号
VERSION=$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
DOCKER_USERNAME=${1:-"jeweis"}  # 默认用户名，可以通过参数覆盖

# 镜像名称
IMAGE_NAME="mcpcat"
FULL_IMAGE_NAME="${DOCKER_USERNAME}/${IMAGE_NAME}"

echo -e "${BLUE}🐳 MCPCat Docker 构建和发布脚本${NC}"
echo -e "${BLUE}版本: ${VERSION}${NC}"
echo -e "${BLUE}镜像: ${FULL_IMAGE_NAME}${NC}"
echo ""

# 检查Docker是否运行
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}❌ Docker 未运行，请先启动 Docker${NC}"
    exit 1
fi

# 检查是否已登录Docker Hub
if ! docker info | grep -q "Username"; then
    echo -e "${YELLOW}⚠️  请先登录 Docker Hub:${NC}"
    echo "docker login"
    exit 1
fi

echo -e "${YELLOW}🔨 开始构建镜像...${NC}"

# 构建镜像
docker build -t "${FULL_IMAGE_NAME}:${VERSION}" -t "${FULL_IMAGE_NAME}:latest" .

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ 镜像构建成功${NC}"
else
    echo -e "${RED}❌ 镜像构建失败${NC}"
    exit 1
fi

# 显示镜像信息
echo -e "${BLUE}📋 镜像信息:${NC}"
docker images "${FULL_IMAGE_NAME}" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"

echo ""
echo -e "${YELLOW}🚀 开始推送镜像到 Docker Hub...${NC}"

# 推送版本标签
echo -e "${BLUE}推送版本标签: ${VERSION}${NC}"
docker push "${FULL_IMAGE_NAME}:${VERSION}"

# 推送latest标签
echo -e "${BLUE}推送 latest 标签${NC}"
docker push "${FULL_IMAGE_NAME}:latest"

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}🎉 镜像发布成功！${NC}"
    echo ""
    echo -e "${BLUE}📦 可用镜像:${NC}"
    echo "  • ${FULL_IMAGE_NAME}:${VERSION}"
    echo "  • ${FULL_IMAGE_NAME}:latest"
    echo ""
    echo -e "${BLUE}🚀 使用方法:${NC}"
    echo "  docker run -d -p 8000:8000 -v \$(pwd)/config.json:/app/config.json ${FULL_IMAGE_NAME}:latest"
    echo ""
    echo -e "${BLUE}🌐 Docker Hub 链接:${NC}"
    echo "  https://hub.docker.com/r/${DOCKER_USERNAME}/${IMAGE_NAME}"
else
    echo -e "${RED}❌ 镜像推送失败${NC}"
    exit 1
fi