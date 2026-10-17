# FastMCP 4.1 升级恢复演练

2026-10-15，隔离回环环境已完成旧程序 → 升级写入 → 完整快照 → 匹配旧程序恢复的串联验收。不是生产切换、发布或降级线上数据库；临时数据在探针退出时清除。生产操作者必须自行保留快照，不能照搬测试的自动清理策略。

## 已验证结果

以下为取消平台开关前的历史演练记录。当前版本已固定提供 Skills/Apps/CodeMode，旧开关字段不再阻止访问；恢复时必须停止实例接入和业务生命周期，不能使用 enabled=false 代替停机边界。

旧程序来自 Git `v1.0.1`，commit `a620e4841ebd2e92d8326d965e02e75cd825b8a2` 的干净导出。旧虚拟环境为 Python 3.11.14、FastMCP 3.4.4、MCP SDK 1.25.0；升级环境为 FastMCP 4.1.0 / SDK 2.3.0。没有修改旧源码或给旧程序安装新 SDK。

`scripts/probe_upgrade_recovery.py` 与可信子进程 `scripts/fixtures/recovery_legacy_host.py` 验证：

1. 以固定原 0001/0002 DDL 建库，保留九张旧业务表、四种 Skill 状态、未知配置、合成 API/OAuth 凭证、生成来源和原 ZIP/bundle。禁用合成外部来源，只启动可信本机 echo 上游。
2. 旧程序及旧 SDK 经原 HTTP/SSE 地址调用 echo；通过旧管理 API 发布 local 1.0.0。停止旧服务形成停写边界，SQLite backup API 保存数据库，连同制品、配置保存升级前快照（10 个文件）。不能在 WAL 打开时仅移动 `.db` 文件。
3. 升级到 `0005_app_render_review`，发布 local 1.0.1、remote-only 2.0.0，上传新的 HTML App，保存非默认 CodeMode 限额和未知字段；真实启动升级服务，原 Registry latest 仍是 local 1.0.1。
4. 停止升级服务和全部业务生命周期，离线关闭 Skills/Apps/CodeMode 新入口但保留其他设置；保存升级后完整快照（14 个文件），包括新 App 制品。这里采用停机维护实现停写，不宣称有在线“只读维护模式”产品功能。
5. 复制升级前匹配快照至独立恢复目录，由原旧程序启动。启动前/后九表逐列相等，旧配置完全相等、无 CodeMode 默认回写，所有旧制品 SHA 不变；旧 SDK 再次经 HTTP 和 SSE 成功调用。
6. 公共 npm `@jeweis/mcpcat@1.0.1`（锁定来源/integrity）完成 10 次 CLI 命令：connect、list、info、install、pin、update、unpin、update、rollback、doctor。pin 阻止更新，unpin 后安装 local 1.0.1，rollback 从客户本地备份恢复 1.0.0。只使用临时 Codex project 目录、环境凭证，不碰系统 Keychain 或用户 Agent 目录。
7. 升级后快照仍可读，保留三个 Skill 版本、HTML App 和文件、关闭开关后的非默认 CodeMode 设置及未知字段；整份快照逐文件 SHA 与恢复前相等。新写入没有被恢复副本覆盖或静默丢弃。

最终输出 `result=passed`、`mcp_http_sse=passed`、`cli_commands=10`、`old_tables_equal=true`、`post_upgrade_preserved=true`、`new_endpoints_disabled=true`。旧 CLI 对升级服务的全部 10 Agent × 两个 Scope 回归另见 [公共 CLI 验收](published-cli-upgrade-verification.md)；本演练不重复宣称第三方 Agent 激活或 Windows/Linux 实机通过。

## 复现

仅针对可信仓库导出和一次性临时数据；四个参数必须是已审核的旧 Python、原 Tag 导出、Node 24+、公共 CLI 安装目录：

```bash
git archive v1.0.1 | tar -x -C <预先创建的临时旧源码目录>
python scripts/probe_upgrade_recovery.py <旧环境/bin/python> <临时旧源码目录> <Node24可执行文件> <公共CLI安装目录>
```

需要本机回环监听权限，不需要外网业务连接。公共 CLI 安装与 integrity 复核方法见其独立验收文档。旧 `venv/bin/python` 参数保留符号链接路径，不能先解析到全局 Python 而绕过虚拟环境。进程启动、命令和服务关闭均有有界等待；只终止自己启动的临时子进程。脚本只生成合成夹具，不接受用户实际数据库作为输入。

## 生产恢复操作顺序

- 升级前先停止所有写入口和服务实例，记录程序/镜像版本、依赖锁、数据库 revision、配置导出、制品目录及必要加密配置。数据库使用一致性备份；制品必须与数据库同一停写边界，快照限制文件访问并单独保存校验值。
- 先在副本验证升级，再决定上线；当前平台能力固定提供，但不自动发布资源。不要把协议无状态理解为本项目已经支持多个 worker 共同操作 manager。
- 需要回退时先停止实例接入、写入与全部服务，完整保存升级后的数据库、Skill/App 制品、配置、版本和审计。明确备份后新写入的负责人及重放计划；没有自动合并/反向迁移工具。
- 使用旧程序匹配的升级前数据库、制品及配置启动独立恢复副本。验证原 MCP、认证、历史 ZIP/bundle 和 CLI pin/rollback 后再切换入口。
- 不把新 head 交给旧 Alembic，不手改 revision，不靠 `pip install fastmcp==3.4.4` 伪装数据回退。已完成的上游副作用不因数据库恢复而撤销，需单独核对。
- 保留升级后快照供导出/审计/重放。未完成新写入的处置前不得删除它；恢复验收通过也不是删除生产快照的授权。
