# mcpcat 1.1.0

## 新功能

- 升级 FastMCP 4.1.0 与 MCP SDK 2.3.0，提供现代 MCP 协议及官方 Skills 扩展，同时保留旧 HTTP/SSE 客户端。
- Skills 中心支持统一文件夹/ZIP 上传、本地与远程内容、版本审核、完整文件浏览及远程发现/读取/目录读取；保留 ZIP 下载和 CLI 安装。上传脚本仅分发，不在 mcpcat 执行。
- MCP Apps 中心统一展示平台托管和 MCP 服务自身提供的应用，支持上传、审核、工具绑定、隔离预览与明确授权的真实交互。
- MCP 测试台统一工具、资源、提示模板、Skills 和应用，参数默认使用 Schema 表单，保留 JSON 高级模式，以及订阅、客户端交互和授权诊断。
- 独立 CodeMode 入口提供有界沙箱编排，每次底层工具调用仍检查权限；原 Catalog 与工具接口保留。
- CLI 新增 MCP 服务、服务端 Skills 和 Apps 管理命令，保留原本地安装/更新/pin/rollback 命令，不提供测试或 App 渲染命令。
- 定向修复 Python 依赖安全问题及 SSE 生命周期兼容问题。

## 安装与升级

```bash
npm install -g @jeweis/mcpcat@1.1.0
docker pull jeweis/mcpcat:1.1.0
```

CLI 使用方法见 [用户文档](../user/cli.md)。Docker 升级保留原数据卷、端口及认证配置，不使用删除数据卷的命令。升级前停止写入，保存数据库、制品、配置及对应旧镜像；回滚须恢复匹配快照，不能只降级镜像继续读取已升级数据库。

## 兼容与安全

统一应用、内置 Web、CLI 和 Docker 版本为 1.1.0。Registry API v1、Schema 1.0.0、最低 CLI 0.1.0 不变，旧 CLI 1.0.1 仍可使用原本地安装流程。历史 ZIP、已发布内容及摘要不改写，新远程内容需要明确审核发布。资源级权限、工具绑定、沙箱及 CSP 限制继续生效，支持的功能固定提供，不以平台开关隐藏。

升级前已完成后端全量 1333 项、Python 3.10 专项 343 项、CLI 129 项、Flutter 148 项回归；修复后的实际 Docker 镜像 108 个 Python 包审计无已知漏洞，未忽略公告。浏览器只读身份的真实时间查询与宿主确认已通过。详见 [安全修复验收](security-dependency-remediation.md)；审计不等于全平台、OS 或公网安全认证。

发布版本变更后重新执行版本/分发/CLI/协议门禁并重建 Web；历史验收中的“未推送/未发布”为当时状态，最终公共发布结果以 GitHub Release、npm Registry 与 Docker Hub 为准。此前提案的外部 beta/回滚演练不因本次正式发布自动视为完成。
