# Skills 协议验收与客户端矩阵

验证日期：2026-10-15。升级仍在实施，本页不表示已经发布。服务器协议一致性与客户端激活是不同验收项。

## 固定版本

机器可读版本见 [skills-conformance.json](../../scripts/fixtures/skills-conformance.json)。

| 项目 | 固定版本/来源 |
| --- | --- |
| 基础 MCP | 2026-07-28 |
| FastMCP / Python SDK | 4.1.0 / 2.3.0 |
| Skills 扩展 | io.modelcontextprotocol/skills |
| 规范 | [ext-skills 167da6c 的 stable/skills.mdx](https://github.com/modelcontextprotocol/ext-skills/blob/167da6c0d42c247a8f8470228a10ca711e3f6c45/specification/stable/skills.mdx) |
| 官方一致性场景 | [conformance c37eec8](https://github.com/modelcontextprotocol/conformance/tree/c37eec888e1c6ff140af79987a40008548b7cc5f)，0.2.0-alpha.12 |
| 真实协议检查客户端 | 公共 npm @modelcontextprotocol/inspector 2.11.0；tarball integrity 见版本文件 |

固定 SDK 的 CacheableResult 使用 `cacheScope: private`，不是自行发明的枚举。mcpcat 的 list/get 使用 `resultType: complete`、`ttlMs: 0`、`cacheScope: private`；Resources 结果由固定框架按协议输出缓存字段。HTTP 另设 `Cache-Control: private, no-store` 和认证头 Vary。它们不能撤回客户端已经取得的文件。

## 官方场景结果

使用真正的应用工厂、独立 Skills 挂载、临时 SQLite/制品和真实 TCP，不替换 Registry/协议实现。三个公开审核的合成 Skill 强制一页一个，每个含 SKILL.md、CRLF 中文参考、600000 字节二进制及不执行的脚本；不启动用户 MCP 或访问用户凭证。

| 官方场景 | SUCCESS | WARNING/FAILURE | SKIPPED |
| --- | ---: | ---: | ---: |
| sep-2640-skills-enumeration | 32 | 0 | 0 |
| sep-2640-skills-manifest | 6 | 0 | 0 |
| sep-2640-skills-directory | 1 | 0 | 6 |

合计 39 个成功检查，包括逐场景 wire schema 检查；可选 directoryRead 未声明，6 项跳过，不能称作目录读取通过。分页、完整 entry、直接 get、未知 URI、frontmatter、资源元数据及限制均走原始官方场景。

官方 runner 默认能力不含 Skills。适配脚本只通过公开 RunContext.connect 的 options.capabilities 声明客户端 opt-in；不改场景、断言、服务器响应或私有 dispatcher。执行前核对官方源码 HEAD、工作区 diff 和包版本。原 FastMCP 自有裸 fastmcp 元数据触发了规范命名建议，现仅对 mcpcat 自有 Skill Resource 用公开 get_meta 覆盖为 io.modelcontextprotocol.skills/fastmcp；不改普通/上游资源。

## 真实客户端支持矩阵

“未验证”不等于“不支持”。第三方产品更新后需重跑，而不能从官方清单的勾选推断与本项目互通。

| 客户端/Host | 本次证据 | 边界 |
| --- | --- | --- |
| Inspector CLI 2.11.0 | 公共包，modern 协议，Skills opt-in；分页 list --verify 验证 3 个 Skill/12 个文件，随后逐个直接 get --verify 全通过 | 是协议/摘要检查，不是 Agent 激活或本地脚本执行 |
| Inspector CLI 2.6.0 | 实际执行；默认 legacy 不声明 Skills，modern 下旧客户端结果解析失败 | 不列为互通通过，不修改标准响应迁就旧检查器 |
| FastMCP Client 4.1.0 / SDK 2.3.0 与现代原始请求 | 仓库 ASGI/网络回归覆盖扩展 opt-in、Resources 和旧协议 | 通用 SDK 不是自动激活 Skills 的 Agent |
| mcporter 0.13.7 | 原 MCP tools 调用已通过四类上游矩阵 | 保留 local 生成模板用途，不宣称支持官方远程 Skills 激活 |
| mcpcat CLI 1.0.1 | 本地 ZIP 安装契约保留；公共包完整生命周期独立验收 | 不是 MCP Skills Host |
| 其他 Agent/Host | 以[官方客户端矩阵](https://modelcontextprotocol.io/extensions/client-matrix)和[固定实现列表](https://github.com/modelcontextprotocol/ext-skills/blob/167da6c0d42c247a8f8470228a10ca711e3f6c45/docs/implementations.md)定位候选，本项目未作激活实测 | 继续使用 ZIP/CLI；不承诺连接即启用 |

Inspector 测试通过只读 mcp.json 使用公开 protocolEra=modern 和 per-server metadata 声明 io.modelcontextprotocol/clientCapabilities.extensions。2.6.0 的 advertisedExtensions 设置本身不足以让 CLI 发送 Skills opt-in。最终探针将 HOME、client-config、OAuth 路径与临时配置隔离，并明确设置 MCP_INSPECTOR_SECRET_STORE=memory，避免仅更换 HOME 却仍使用操作系统 Keychain；测试公开端点只含合成文件，不代表建议在生产关闭认证。

## Gate C 的安全与竞态证据

以下是本地补充回归，不冒充官方测试场景：

- [test_remote_skill_protocol.py](../../tests/test_remote_skill_protocol.py)：原字节 SHA/size、全部 frontmatter、二进制与大文件、512 文件/16 MiB 包含边界、分页快照、直接历史 URI、opt-in 和 legacy 原资源。
- [test_skills_mcp_gateway.py](../../tests/test_skills_mcp_gateway.py)：正式应用鉴权、匿名公开审核、Key 撤销、READ/WRITE 不能共用游标、认证头/缓存隔离、运行关闭及路径冲突。
- [test_skills_source_gateway.py](../../tests/test_skills_source_gateway.py)：服务/Catalog/独立端点作用域，动态目录、停止/删除/重建身份与 WRITE 不能读草稿。
- [test_skills_protocol_security.py](../../tests/test_skills_protocol_security.py)：11 类恶意 URI 分别经 get/read 拒绝；发现后篡改摘要/frontmatter 立即拒绝旧 URI；真实写线程提交前，HTTP 读取仍得到完整旧快照，提交后新列表才选新版本，原 URI 不变。
- [test_skill_content_workflow.py](../../tests/test_skill_content_workflow.py) 与 [test_skill_upload_recovery.py](../../tests/test_skill_upload_recovery.py)：冻结批次、编辑/发布互斥、损坏/缺失制品、上传失败恢复、脚本不执行、旧 local 及 remote-only 隔离。

Gate C 判定这些固定场景和服务器分发契约通过；不包含 Agent 自动激活、真实脚本执行、第三方信任审批 UI 或可选目录读取。

## 复现

使用项目锁定的 Python 环境和 Node 22.19+。只在临时工具目录安装测试依赖，不加入生产依赖，不给第三方测试工具真实凭证。官方 conformance 锁文件目前有依赖审计提示；本次只在回环测试夹具运行，未执行 npm 安装脚本，不自动升级官方锁文件来改变已固定的断言环境。

```bash
git clone https://github.com/modelcontextprotocol/conformance.git /tmp/mcpcat-conformance
git -C /tmp/mcpcat-conformance checkout c37eec888e1c6ff140af79987a40008548b7cc5f
npm ci --ignore-scripts --prefix /tmp/mcpcat-conformance
npm install --ignore-scripts --prefix /tmp/mcpcat-inspector @modelcontextprotocol/inspector@2.11.0
python scripts/probe_official_skills.py /tmp/mcpcat-conformance /tmp/mcpcat-inspector
pytest tests/test_remote_skill_protocol.py tests/test_skills_mcp_gateway.py tests/test_skills_source_gateway.py tests/test_skills_protocol_security.py
```

探针退出时关闭端口、应用/协议任务、数据库和子进程。工具安装目录由维护者用完后清理；不要把临时测试通过当成运行实例已升级。
