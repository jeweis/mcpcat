# mcpcat 产品概述

mcpcat 是一个基于 Python 的 MCP（Model Context Protocol）聚合平台，提供多种 MCP 协议的统一管理和运行。

## 核心目标
- 通过单一接口聚合和管理多个 MCP 服务器
- 支持多种 MCP 传输协议（STDIO、SSE、Streamable HTTP、OpenAPI）
- 提供基于 Web 的 MCP 服务管理控制台
- 实现安全的服务隔离和权限控制

## 主要功能
- 基于 FastAPI 构建的高性能 Web 服务
- 多协议 MCP 支持，提供统一 API
- 带健康监控的 Web 管理界面
- Docker 容器化，便于部署
- 自动生成文档的 RESTful API

## 目标用户
- 使用 MCP 协议的开发者
- 管理 MCP 服务的系统管理员
- 需要 MCP 服务聚合的应用程序

## 当前版本
v0.1.1 - 积极开发中，专注于核心 MCP 协议支持和服务管理界面。