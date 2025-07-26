@echo off
REM mcpcat Docker 构建和发布脚本 (Windows版本)
REM 使用方法: build-and-push.bat [your-dockerhub-username]

setlocal enabledelayedexpansion

REM 获取版本号
for /f "tokens=3 delims= " %%a in ('findstr "^version = " pyproject.toml') do (
    set VERSION_RAW=%%a
)
set VERSION=%VERSION_RAW:"=%

REM 设置Docker用户名 (默认为jeweis，可通过参数覆盖)
if "%1"=="" (
    set DOCKER_USERNAME=jeweis
) else (
    set DOCKER_USERNAME=%1
)

REM 镜像名称
set IMAGE_NAME=mcpcat
set FULL_IMAGE_NAME=%DOCKER_USERNAME%/%IMAGE_NAME%

echo.
echo 🐳 mcpcat Docker 构建和发布脚本
echo 版本: %VERSION%
echo 镜像: %FULL_IMAGE_NAME%
echo.

REM 检查Docker是否运行
docker info >nul 2>&1
if errorlevel 1 (
    echo ❌ Docker 未运行，请先启动 Docker
    exit /b 1
)

echo 🔨 开始构建镜像...
echo.

REM 构建镜像
docker build -t "%FULL_IMAGE_NAME%:%VERSION%" -t "%FULL_IMAGE_NAME%:latest" .

if errorlevel 1 (
    echo ❌ 镜像构建失败
    exit /b 1
)

echo ✅ 镜像构建成功
echo.

REM 显示镜像信息
echo 📋 镜像信息:
docker images "%FULL_IMAGE_NAME%" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"

echo.
echo 🚀 开始推送镜像到 Docker Hub...
echo.

REM 推送版本标签
echo 推送版本标签: %VERSION%
docker push "%FULL_IMAGE_NAME%:%VERSION%"

if errorlevel 1 (
    echo ❌ 版本标签推送失败
    exit /b 1
)

REM 推送latest标签
echo 推送 latest 标签
docker push "%FULL_IMAGE_NAME%:latest"

if errorlevel 1 (
    echo ❌ latest标签推送失败
    exit /b 1
)

echo.
echo 🎉 镜像发布成功！
echo.
echo 📦 可用镜像:
echo   • %FULL_IMAGE_NAME%:%VERSION%
echo   • %FULL_IMAGE_NAME%:latest
echo.
echo 🚀 使用方法:
echo   docker run -d -p 8000:8000 -v "%cd%\.mcpcat:/app/.mcpcat" %FULL_IMAGE_NAME%:latest
echo.
echo 🌐 Docker Hub 链接:
echo   https://hub.docker.com/r/%DOCKER_USERNAME%/%IMAGE_NAME%

pause