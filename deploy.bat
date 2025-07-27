@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo 🚀 开始部署 mcpcat...

REM 检查 Docker 是否安装
docker --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Docker 未安装，请先安装 Docker Desktop
    pause
    exit /b 1
)

REM 检查 Docker Compose 是否可用
docker-compose --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Docker Compose 未安装，请先安装 Docker Compose
    pause
    exit /b 1
)

REM 检查配置文件是否存在
echo 📋 检查配置文件...
if not exist ".mcpcat\config.json" (
    echo ⚠️ 配置文件 .mcpcat\config.json 不存在
    echo 正在创建默认配置文件...
    mkdir .mcpcat 2>nul
    (
        echo {
        echo   "mcpServers": {},
        echo   "security": {
        echo     "api_keys": [],
        echo     "auth_header_name": "Mcpcat-Key"
        echo   },
        echo   "app": {
        echo     "version": "0.1.1",
        echo     "log_level": "INFO",
        echo     "enable_metrics": true
        echo   }
        echo }
    ) > .mcpcat\config.json
    echo ✅ 已创建默认配置文件 .mcpcat\config.json
    echo 💡 提示：您可以编辑 .mcpcat\config.json 文件来配置 MCP 服务器
) else (
    echo ✅ 配置文件 .mcpcat\config.json 已存在
)

REM 停止现有容器（如果存在）
echo 🛑 停止现有容器...
docker-compose down >nul 2>&1

REM 构建镜像
echo 🔨 构建 Docker 镜像...
docker-compose build --no-cache
if errorlevel 1 (
    echo ❌ 镜像构建失败
    pause
    exit /b 1
)

REM 启动服务
echo ▶️ 启动服务...
docker-compose up -d
if errorlevel 1 (
    echo ❌ 服务启动失败
    pause
    exit /b 1
)

REM 等待服务启动
echo ⏳ 等待服务启动...
timeout /t 10 /nobreak >nul

REM 检查服务状态
echo 🔍 检查服务状态...
docker-compose ps | findstr "Up" >nul
if errorlevel 1 (
    echo ❌ 部署失败，请检查日志:
    docker-compose logs
    pause
    exit /b 1
) else (
    echo ✅ mcpcat 部署成功！
    echo 📱 访问地址: http://localhost:8000
    echo 🏥 健康检查: http://localhost:8000/api/health
    echo.
    echo 📋 常用命令:
    echo   查看日志: docker-compose logs -f
    echo   停止服务: docker-compose down
    echo   重启服务: docker-compose restart
    echo   查看状态: docker-compose ps
)

echo.
echo 按任意键退出...
pause >nul