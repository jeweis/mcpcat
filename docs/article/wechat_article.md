# 🚀 告别繁琐配置！mcpcat：你的 AI 模型 MCP 聚合管家

在这个 AI Agent 爆发的时代，Anthropic 推出的 **MCP (Model Context Protocol)** 协议彻底改变了 LLM 连接世界的方式。

但是，随着你收集的 MCP 工具越来越多（文件操作、GitHub、数据库、本地脚本...），你是否也遇到了这些烦恼？

*   😫 **配置地狱**：Claude Desktop 要配一遍，Cursor 要配一遍，Windsurf 还要配... 每次更新都要改好几个配置文件。
*   🔗 **连接受限**：很多好用的 MCP server 是基于 `stdio`（标准输入输出）的，这意味着它们只能在本地跑，没法部署到服务器上远程调用。
*   🔒 **安全裸奔**：想把自己的工具分享给团队成员，却发现没有鉴权机制，不敢公开。

如果这是你的现状，那么 **mcpcat** 就是为你量身定制的开源神器！

---

## 🐱 什么是 mcpcat？

**mcpcat** 是一个轻量级、高性能的 **MCP 聚合平台**。

你可以把它想象成 **AI 工具界的 Nginx**。它负责连接你所有的 MCP 服务器（无论是本地的命令行工具，还是远程的 SSE 服务），并将它们整合成一个统一的、安全的 HTTP/SSE 接口。

有了它，你的 AI 客户端（如 Claude、IDE）只需要连接 **mcpcat** 一个地址，就能使用你挂载的所有工具！

---

## ✨ 核心黑科技

### 1. 🔄 万物皆可 Web 化 (Stdio -> HTTP/SSE)
这是 mcpcat 最酷的功能！它能将本地的 `stdio` 类型 MCP server（比如官方的 `fetch`、`filesystem`）自动包装成标准的 **HTTP** 或 **SSE (Server-Sent Events)** 服务。
**这意味着：** 你可以在一台强劲的服务器上运行各种本地工具，然后通过网络让你的笔记本、手机甚至网页版的 AI 助手安全地调用它们。

### 2. 🛡️ 统一鉴权网关
原生 MCP 往往缺乏安全层。mcpcat 内置了完善的 **API Key 管理机制**。
*   支持 Read/Write 权限分离。
*   自动拦截未授权请求。
*   让你放心地将 AI 能力暴露在公网。

### 3. 🔌 热插拔与动态管理
想添加一个新的工具？不需要重启服务！
mcpcat 支持**热重载**和**动态配置**。你可以通过 API 随时添加、删除或重启某个 MCP 服务，业务零中断。

### 4. 🧩 协议大一统
不管你的源头是：
*   命令行工具 (`stdio`)
*   远程 SSE 流 (`sse`)
*   标准 HTTP 服务 (`streamable-http`)
*   甚至是一个 OpenAPI 文档 (`openapi`)

mcpcat 都能把它们吃进去，吐出统一标准的接口供 AI 调用。

### 5. 📄 OpenAPI 一键接入 (Swagger -> MCP)
这是 mcpcat 的杀手级功能！
你不需要写一行代码，只要提供一个 OpenAPI (Swagger) 文档地址，mcpcat 就能自动把它转换成 MCP 工具集。
*   **Header 透传**：支持配置自定义 Headers（如 `X-API-Key` 或 `Private-Token`）。这意味着你可以轻松把公司内部需要鉴权的 API（如 Jira、GitLab、CRM）直接喂给 AI，让 AI 帮你查 Bug、提单、查数据。（注：避免使用 `Authorization` 以防止被网关拦截）

### 6. 🤝 团队协作，释放算力
还在让团队每个人的电脑都跑一遍 Docker 和 Python 环境吗？
*   **统一部署**：只需在公司服务器部署一套 mcpcat，全员共享。
*   **释放资源**：将重负载的工具（如数据库分析、大代码库检索）移至服务器，让开发者的笔记本保持轻快，不再因为运行本地模型服务而发烫卡顿。
*   **开箱即用**：新同事入职，只需填入 URL 和 Key，立刻拥有全套团队标准化的 AI 工具链，无需耗时搭建环境。

---

## ⚡️ 1分钟快速上手

最简单的启动方式是使用 Docker。一行命令即可启动，系统会自动生成 API Key。

### 🚀 启动命令

```bash
docker run -d \
  --name mcpcat \
  -p 8000:8000 \
  -v mcpcat_data:/app/.mcpcat \
  --restart unless-stopped \
  jeweis/mcpcat:latest
```

### 🔑 获取 API Key
启动成功后，打开浏览器访问 `http://localhost:8000`，你将在控制台页面看到自动生成的 API Key。

### 🔌 连接 AI
现在，你的所有工具都可以通过这就一个地址访问了！

*   **SSE 地址**: `http://localhost:8000/sse/web-search` (推荐)
*   **HTTP 地址**: `http://localhost:8000/mcp/web-search`
*   **鉴权头**: `Mcpcat-Key: <你的Key>`

在 Claude Desktop 或 Cursor 中配置这一个地址，即可让 AI 拥有联网搜索和操作 Git 的能力。

---

## 🌟 为什么选择 mcpcat？

*   **配置一次，到处运行**：只需维护一份配置，所有 AI 客户端共享，效率翻倍。
*   **开源免费**：代码完全透明，可私有化部署，数据握在自己手里。
*   **即装即用**：完善的 Docker 支持，一键启动，零基础也能轻松驾驭。

让 AI 工具的管理回归简单。从今天起，用 **mcpcat** 驯服你的 MCP 动物园！

---

### 👇 获取项目

喜欢这个项目吗？欢迎来 GitHub 点个 Star ⭐️ 支持一下！

**GitHub 地址**: https://github.com/jeweis/mcpcat

