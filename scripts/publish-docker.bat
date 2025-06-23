@echo off
setlocal enabledelayedexpansion

REM MCPCat Docker 镜像发布脚本 (Windows 版本)
REM 用法: scripts\publish-docker.bat [version] [docker-username]

set "VERSION=%~1"
set "DOCKER_USERNAME=%~2"

if "%VERSION%"=="" set "VERSION=latest"

if "%DOCKER_USERNAME%"=="" (
    echo [ERROR] 请提供 Docker Hub 用户名
    echo 用法: %0 [version] ^<docker-username^>
    echo 示例: %0 v1.0.0 myusername
    exit /b 1
)

set "IMAGE_NAME=%DOCKER_USERNAME%/mcpcat"
set "FULL_IMAGE_NAME=%IMAGE_NAME%:%VERSION%"
set "LATEST_IMAGE_NAME=%IMAGE_NAME%:latest"

echo [INFO] 开始构建和发布 MCPCat Docker 镜像
echo [INFO] 版本: %VERSION%
echo [INFO] 镜像名称: %FULL_IMAGE_NAME%

REM 检查 Docker 是否安装
docker --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker 未安装或不在 PATH 中
    exit /b 1
)

REM 检查 Docker 是否运行
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker 服务未运行
    exit /b 1
)

REM 检查是否已登录 Docker Hub
docker info | findstr "Username" >nul
if errorlevel 1 (
    echo [WARNING] 未登录 Docker Hub，请先登录
    docker login
    if errorlevel 1 (
        echo [ERROR] Docker Hub 登录失败
        exit /b 1
    )
)

REM 检查 buildx 是否可用
docker buildx version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker buildx 不可用，请更新 Docker 到最新版本
    exit /b 1
)

REM 创建并使用多架构构建器
echo [INFO] 设置多架构构建环境
docker buildx ls | findstr "multiarch" >nul
if errorlevel 1 (
    docker buildx create --name multiarch --driver docker-container --use
) else (
    docker buildx use multiarch
)

docker buildx inspect --bootstrap
if errorlevel 1 (
    echo [ERROR] 构建器初始化失败
    exit /b 1
)

REM 构建选项
set "DOCKERFILE=Dockerfile"
set "PLATFORMS=linux/amd64,linux/arm64"

REM 检查是否使用生产环境 Dockerfile
if exist "Dockerfile.production" (
    set /p "use_production=是否使用生产环境 Dockerfile? (y/N): "
    if /i "!use_production!"=="y" (
        set "DOCKERFILE=Dockerfile.production"
        echo [INFO] 使用生产环境 Dockerfile
    )
)

REM 构建标签列表
set "TAGS=-t %FULL_IMAGE_NAME%"
if not "%VERSION%"=="latest" (
    set "TAGS=!TAGS! -t %LATEST_IMAGE_NAME%"
)

REM 添加语义化版本标签
echo %VERSION% | findstr /r "^v\?[0-9]\+\.[0-9]\+\.[0-9]\+$" >nul
if not errorlevel 1 (
    for /f "tokens=1,2,3 delims=." %%a in ("%VERSION:v=%") do (
        set "MAJOR=%%a"
        set "MINOR=%%b"
        set "PATCH=%%c"
        set "TAGS=!TAGS! -t %IMAGE_NAME%:!MAJOR!"
        set "TAGS=!TAGS! -t %IMAGE_NAME%:!MAJOR!.!MINOR!"
        set "TAGS=!TAGS! -t %IMAGE_NAME%:!MAJOR!.!MINOR!.!PATCH!"
        set "TAGS=!TAGS! -t %IMAGE_NAME%:!MAJOR!.!MINOR!.!PATCH!-python3.13"
    )
)

echo [INFO] 构建多架构 Docker 镜像
echo [INFO] 平台: %PLATFORMS%
echo [INFO] Dockerfile: %DOCKERFILE%
echo [INFO] 标签: %TAGS%

REM 构建并推送镜像
docker buildx build --platform %PLATFORMS% --file %DOCKERFILE% %TAGS% --push .

if errorlevel 1 (
    echo [ERROR] 镜像构建或推送失败
    exit /b 1
) else (
    echo [SUCCESS] 镜像构建和推送成功!
    echo [INFO] 镜像已发布到: %FULL_IMAGE_NAME%
    
    if not "%VERSION%"=="latest" (
        echo [INFO] 同时更新了 latest 标签: %LATEST_IMAGE_NAME%
    )
    
    echo.
    echo [INFO] 使用以下命令运行镜像:
    echo docker run -p 8000:8000 %FULL_IMAGE_NAME%
    echo.
    echo [INFO] 或使用 docker-compose:
    echo docker-compose up -d
)

REM 可选：运行安全扫描
set /p "run_scan=是否运行安全扫描? (y/N): "
if /i "%run_scan%"=="y" (
    echo [INFO] 运行安全扫描...
    
    REM 使用 Docker Scout (如果可用)
    docker scout --version >nul 2>&1
    if not errorlevel 1 (
        docker scout cves %FULL_IMAGE_NAME%
    ) else (
        REM 或使用 Trivy (如果可用)
        trivy --version >nul 2>&1
        if not errorlevel 1 (
            trivy image %FULL_IMAGE_NAME%
        ) else (
            echo [WARNING] 未找到安全扫描工具 (docker scout 或 trivy)
        )
    )
)

REM 清理构建缓存
set /p "clean_cache=是否清理构建缓存? (y/N): "
if /i "%clean_cache%"=="y" (
    echo [INFO] 清理构建缓存...
    docker buildx prune -f
    echo [SUCCESS] 缓存清理完成
)

echo [SUCCESS] Docker 镜像发布流程完成!
echo [INFO] 查看镜像信息: https://hub.docker.com/r/%DOCKER_USERNAME%/mcpcat

pause