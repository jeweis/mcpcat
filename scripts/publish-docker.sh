#!/bin/bash

# MCPCat Docker 镜像发布脚本
# 用法: ./scripts/publish-docker.sh [version] [docker-username]

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查参数
VERSION=${1:-"latest"}
DOCKER_USERNAME=${2:-""}

if [ -z "$DOCKER_USERNAME" ]; then
    log_error "请提供 Docker Hub 用户名"
    echo "用法: $0 [version] <docker-username>"
    echo "示例: $0 v1.0.0 myusername"
    exit 1
fi

# 镜像名称
IMAGE_NAME="$DOCKER_USERNAME/mcpcat"
FULL_IMAGE_NAME="$IMAGE_NAME:$VERSION"
LATEST_IMAGE_NAME="$IMAGE_NAME:latest"

log_info "开始构建和发布 MCPCat Docker 镜像"
log_info "版本: $VERSION"
log_info "镜像名称: $FULL_IMAGE_NAME"

# 检查 Docker 是否安装
if ! command -v docker &> /dev/null; then
    log_error "Docker 未安装或不在 PATH 中"
    exit 1
fi

# 检查 Docker 是否运行
if ! docker info &> /dev/null; then
    log_error "Docker 服务未运行"
    exit 1
fi

# 检查是否已登录 Docker Hub
if ! docker info | grep -q "Username"; then
    log_warning "未登录 Docker Hub，请先登录"
    docker login
fi

# 检查 buildx 是否可用
if ! docker buildx version &> /dev/null; then
    log_error "Docker buildx 不可用，请更新 Docker 到最新版本"
    exit 1
fi

# 创建并使用多架构构建器
log_info "设置多架构构建环境"
if ! docker buildx ls | grep -q "multiarch"; then
    docker buildx create --name multiarch --driver docker-container --use
else
    docker buildx use multiarch
fi

docker buildx inspect --bootstrap

# 构建选项
BUILD_ARGS=""
DOCKERFILE="Dockerfile"
PLATFORMS="linux/amd64,linux/arm64"

# 检查是否使用生产环境 Dockerfile
if [ -f "Dockerfile.production" ]; then
    read -p "是否使用生产环境 Dockerfile? (y/N): " use_production
    if [[ $use_production =~ ^[Yy]$ ]]; then
        DOCKERFILE="Dockerfile.production"
        log_info "使用生产环境 Dockerfile"
    fi
fi

# 构建标签列表
TAGS="-t $FULL_IMAGE_NAME"
if [ "$VERSION" != "latest" ]; then
    TAGS="$TAGS -t $LATEST_IMAGE_NAME"
fi

# 添加额外标签
if [[ $VERSION =~ ^v?([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    MAJOR=${BASH_REMATCH[1]}
    MINOR=${BASH_REMATCH[2]}
    PATCH=${BASH_REMATCH[3]}
    
    TAGS="$TAGS -t $IMAGE_NAME:$MAJOR"
    TAGS="$TAGS -t $IMAGE_NAME:$MAJOR.$MINOR"
    TAGS="$TAGS -t $IMAGE_NAME:$MAJOR.$MINOR.$PATCH"
    TAGS="$TAGS -t $IMAGE_NAME:$MAJOR.$MINOR.$PATCH-python3.13"
fi

log_info "构建多架构 Docker 镜像"
log_info "平台: $PLATFORMS"
log_info "Dockerfile: $DOCKERFILE"
log_info "标签: $TAGS"

# 构建并推送镜像
docker buildx build \
    --platform $PLATFORMS \
    --file $DOCKERFILE \
    $TAGS \
    --push \
    $BUILD_ARGS \
    .

if [ $? -eq 0 ]; then
    log_success "镜像构建和推送成功!"
    log_info "镜像已发布到: $FULL_IMAGE_NAME"
    
    if [ "$VERSION" != "latest" ]; then
        log_info "同时更新了 latest 标签: $LATEST_IMAGE_NAME"
    fi
    
    echo ""
    log_info "使用以下命令运行镜像:"
    echo "docker run -p 8000:8000 $FULL_IMAGE_NAME"
    echo ""
    log_info "或使用 docker-compose:"
    echo "docker-compose up -d"
else
    log_error "镜像构建或推送失败"
    exit 1
fi

# 可选：运行安全扫描
read -p "是否运行安全扫描? (y/N): " run_scan
if [[ $run_scan =~ ^[Yy]$ ]]; then
    log_info "运行安全扫描..."
    
    # 使用 Docker Scout (如果可用)
    if command -v docker scout &> /dev/null; then
        docker scout cves $FULL_IMAGE_NAME
    # 或使用 Trivy (如果可用)
    elif command -v trivy &> /dev/null; then
        trivy image $FULL_IMAGE_NAME
    else
        log_warning "未找到安全扫描工具 (docker scout 或 trivy)"
    fi
fi

# 清理构建缓存
read -p "是否清理构建缓存? (y/N): " clean_cache
if [[ $clean_cache =~ ^[Yy]$ ]]; then
    log_info "清理构建缓存..."
    docker buildx prune -f
    log_success "缓存清理完成"
fi

log_success "Docker 镜像发布流程完成!"
log_info "查看镜像信息: https://hub.docker.com/r/$DOCKER_USERNAME/mcpcat"