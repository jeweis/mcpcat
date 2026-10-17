# 已发布 CLI 1.0.1 升级兼容验收

任务 6.4 使用公共 npm 的 `@jeweis/mcpcat@1.0.1`，不是当前源码构建或本地 `npm pack`。执行环境为 macOS、Node.js 24.20.0；服务端使用本次升级应用工厂和真实 HTTP Registry、SQLite、ZIP 制品。

## 包身份与复现

固定包地址为 `https://registry.npmjs.org/@jeweis/mcpcat/-/mcpcat-1.0.1.tgz`，npm integrity：

```text
sha512-JkRraLDigA65XAwjyi1NV3PsaK1H7VqYZDqxDJMMBhTF4CZhBJUBqaawAnQLjKhxDLE+mJd5uILH8kAYEWBMIw==
```

在隔离测试环境安装依赖、进入 `mcpcat/`，将以下占位目录替换为一次性临时目录：

```bash
npm install --prefix <临时安装目录> --registry=https://registry.npmjs.org --ignore-scripts --no-audit --no-fund @jeweis/mcpcat@1.0.1
python scripts/probe_published_cli.py <Node-24或更高版本可执行文件> <临时安装目录>
```

安装时 npm 校验公共包完整性，探针再检查 lockfile 的版本、来源和 integrity。实际 CLI `--version --json` 已确认返回 1.0.1。本次 shell 默认 Node 22 不满足包要求，因此验收明确改用 Node 24.20.0，不把 engine 警告当作兼容成功。

探针创建临时 HOME、cwd、Profile、安装记录、数据库和制品；所有 API Key 均为合成测试值，只通过环境变量提供，connect 返回 environment 持久化方式。不写入系统 Keychain，也不操作用户实际 Agent 目录。子进程 PATH 限制为指定 Node 目录及系统默认路径，不运行用户 PATH 中的 Agent/mcporter。

## 结果

10 个 Agent × user/project 两个 Scope，共 **20 组、200 次 CLI 命令全部通过**。每组执行 connect、skills list/info/install/pin/update/unpin/update/rollback 和 doctor。

| Agent | 用户目录（相对 HOME） | 项目目录（相对 cwd） |
| --- | --- | --- |
| codex、pi、dsh、cursor | .agents/skills | .agents/skills |
| claude | .claude/skills | .claude/skills |
| openclaw | .openclaw/skills | skills |
| workbuddy | .workbuddy/skills | .workbuddy/skills |
| codebuddy | .codebuddy/skills | .codebuddy/skills |
| qoder | .qoder/skills | .qoder/skills |
| generic | 显式临时 custom-skills | 显式临时 custom-skills |

每组都核对实际落盘 VERSION：先安装 local 1.0.0，服务端发布 local 1.0.1 和 remote-only 2.0.0；pin 后 update 保持 1.0.0，unpin 后仅更新至 local 1.0.1，不误取远程 2.0.0；rollback 恢复 1.0.0。包内脚本仅落盘，不执行。共享 `.agents` 的 Agent 使用不同临时 HOME/项目，避免覆盖导致假阳性。

doctor 的 Node、Profile、凭证、API、安装路径及残留锁检查均无 error；未安装 mcporter 时明确返回 warn，整体 healthy=true。这里不把 mcporter 警告当作已验收远程业务工具调用；mcporter 0.13.7 的真实代理兼容由 Gate A 单独验证。

此结果证明已发布 CLI 对升级后旧 v1/local Registry 契约的兼容，不证明第三方 Agent 自动发现/激活 Skill、Windows/Linux 实机行为或完整生产升级回退。历史 1.0.1 数据等价另由 Gate B 验证，完整停写/保存升级后数据/恢复匹配旧数据库与制品仍属任务 8.3。
