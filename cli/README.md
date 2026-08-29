# @jeweis/mcpcat

mcpcat Skills Registry 的公开命令行客户端。CLI 与 Python 服务端、Registry
Schema 和各 Skill 独立版本化。要求 Node.js 24 或更高版本，npm 包名为
`@jeweis/mcpcat`，安装后的命令固定为 `mcpcat`。

> 当前仓库仍处于发布准备阶段。npm 包真正发布前，请使用本地 tarball 验证，不能把
> `npm pack` 当成已完成公共发布。

## 安装

正式发布后：

```bash
npm install --global @jeweis/mcpcat
mcpcat --version
```

从仓库验证本地包：

```bash
cd cli
pnpm install --frozen-lockfile
pnpm build
npm pack --ignore-scripts
npm install --global ./jeweis-mcpcat-<version>.tgz
mcpcat --version --json
```

CLI 要求 Node.js `>=24`。使用 `mcpcat doctor` 可以同时检查 Node、Profile、凭证、
Registry API、Agent 目录、安装锁和 mcporter。

## 连接远程 mcpcat 与 Profile

mcpcat 通常部署在远程地址。只需提供实例根地址，不要手工追加 `/api`：

```bash
mcpcat connect https://mcpcat.example.com
mcpcat connect company https://mcpcat.company.example
mcpcat profiles
mcpcat use company
mcpcat skills list --profile company
```

`connect` 会发现 `/api/skills/bootstrap`、验证 Registry 和凭证，并保存不含秘密的
Profile 元数据。首个 Profile 自动成为默认值；`mcpcat use <name>` 切换默认值，
`--profile <name>` 仅覆盖当前命令。

默认仅允许 HTTPS；`localhost`、回环 IPv4 和 `::1` 可以使用 HTTP。其他明文地址
只有显式加入 `--allow-http` 才会连接，并会输出风险警告。

Profile 配置位置：

| 平台 | 配置目录 |
| --- | --- |
| Linux | `${XDG_CONFIG_HOME:-~/.config}/mcpcat` |
| macOS | `~/Library/Application Support/mcpcat` |
| Windows | `%APPDATA%\mcpcat` |

目录内包括 `profiles.json`、`agents.json`、`installations.json`、`locks/` 和
`backups/`。Profile 只保存 base URL、认证头名称及 API/Schema 版本，不保存 API
Key。

## Keychain 与非交互环境

交互式 `connect` 隐藏读取 API Key，并优先通过系统 Keychain 保存；Keychain 的
service 为 `mcpcat`，account 为 Profile 名称。CLI 使用可选依赖
`@napi-rs/keyring` 访问系统 Keychain。

Keychain 不可用或写入失败时，凭证只在当前进程使用，CLI 不会回退为明文文件。
此时重新运行命令应使用环境变量：

```bash
export MCPCAT_URL='https://mcpcat.example.com'
export MCPCAT_API_KEY='<从安全的环境变量或 CI secret 注入>'
mcpcat skills list --json --non-interactive
```

当设置 `MCPCAT_URL` 时必须同时设置 `MCPCAT_API_KEY`，两者优先于本地 Profile。
也可以只设置 `MCPCAT_API_KEY`，让它临时覆盖所选 Profile 的 Keychain 凭证。不要
把 API Key 写入命令参数、日志、仓库或 Profile JSON。

## Agent 与安装目录

```bash
mcpcat agents detect
mcpcat agents list --json
mcpcat agents use codex
mcpcat skills info mysql-tools
mcpcat skills install mysql-tools --agent codex --scope user
mcpcat skills install mysql-tools --agent codex --agent claude --scope project
mcpcat skills install mysql-tools --all-detected-agents
mcpcat skills install mysql-tools --agent generic --target-dir /custom/skills
```

| Agent | ID | user Scope | project Scope |
| --- | --- | --- | --- |
| Codex | `codex` | `~/.agents/skills` | `<project>/.agents/skills` |
| Claude Code | `claude` | `~/.claude/skills` | `<project>/.claude/skills` |
| OpenClaw | `openclaw` | `~/.openclaw/skills` | `<project>/skills` |
| WorkBuddy | `workbuddy` | `~/.workbuddy/skills` | `<project>/.workbuddy/skills` |
| CodeBuddy | `codebuddy` | `~/.codebuddy/skills` | `<project>/.codebuddy/skills` |
| Qoder | `qoder` | `~/.qoder/skills` | `<project>/.qoder/skills` |
| Pi | `pi` | `~/.agents/skills` | `<project>/.agents/skills` |
| DeepSeek Harness | `dsh` | `~/.agents/skills` | `<project>/.agents/skills` |
| Cursor | `cursor` | `~/.agents/skills` | `<project>/.agents/skills` |
| Generic | `generic` | 必须提供 `--target-dir` | 必须提供 `--target-dir` |

检测到多个 Agent 且未设置默认 Agent 时，交互模式会要求选择；CI 或
`--non-interactive` 模式必须显式指定 `--agent` 或
`--all-detected-agents`。`--target-dir` 是高级覆盖入口，Generic Adapter 必填。

安装会下载明确版本、验证 SHA-256 和 Agent Skills 包结构、备份旧目录并原子替换。
Codex、Pi、DeepSeek Harness 和 Cursor 使用同一个 `.agents/skills` 物理目录，因此同一
Scope 下共享实际 Skill 版本；其他不同实际路径拥有独立生命周期。

```bash
mcpcat skills update mysql-tools
mcpcat skills update --all
mcpcat skills pin mysql-tools
mcpcat skills unpin mysql-tools
mcpcat skills rollback mysql-tools
mcpcat skills rollback mysql-tools --version 1.2.0
```

固定版本会被 `update --all` 跳过。无 `--version` 的 rollback 优先使用最新本地
备份；指定版本时，本地备份不存在才从 Registry 下载仍保留的历史版本。

## mcporter 与 MCP Skill

CLI 管理 Skill，但不内嵌或替代 mcporter。MCP 生成的 Skill 使用制品中的
`config/mcporter.json` 连接远程 mcpcat `/mcp/<server>`。当前生成器固定：

- Node.js：`>=24`
- mcporter：`0.13.7`
- 生成器：`1.0.2`

生成的 Skill 指令使用固定版本的 `npx --yes mcporter@0.13.7`。如果希望
`mcpcat doctor` 直接确认 PATH 中的命令，可安装相同版本：

```bash
npm install --global mcporter@0.13.7
mcporter --version
mcpcat doctor
```

MCP Skill 只保存认证头和 `MCPCAT_API_KEY` 占位符，不包含真实凭证。运行 Agent
前应由环境或安全凭证系统提供 `MCPCAT_API_KEY`；需要覆盖制品中的规范实例地址时
可提供 `MCPCAT_URL`。

## CI 与发布边界

CI 使用 Node.js 24 和 pnpm 11.19.0。普通 main/master push 与 Pull Request 只运行
lint、typecheck、test、build、`npm pack --dry-run`、包秘密扫描和 tarball 冒烟，
绝不发布 npm 包。只有 GitHub Release 或 `cli-v<version>` tag 能进入 OIDC +
provenance 发布工作流。

非交互 Registry 操作使用 `MCPCAT_URL`、`MCPCAT_API_KEY`、`--json` 和
`--non-interactive`。完整发布流程见 [RELEASING.md](./RELEASING.md)，版本协商见
[docs/compatibility-matrix.md](./docs/compatibility-matrix.md)。

## 排障

先运行：

```bash
mcpcat doctor --json
```

| 现象/错误码 | 检查与处理 |
| --- | --- |
| `MCPCAT_INSECURE_TRANSPORT` | 使用 HTTPS；只在明确接受风险时对非 localhost 加 `--allow-http`。 |
| `MCPCAT_PROFILE_NOT_FOUND` | 运行 `mcpcat profiles`，重新 `connect` 或使用正确的 `--profile`。 |
| `MCPCAT_AUTH_REQUIRED` / `MCPCAT_AUTH_REJECTED` | 重新 `connect`，检查 Keychain；CI 中同时设置 URL 与 API Key，勿打印 Key。 |
| `MCPCAT_INCOMPATIBLE_API`（退出码 6） | 比对 Bootstrap 的 API、Schema major 和 `min_cli_version`，升级 CLI 或回退服务端。 |
| `MCPCAT_INVALID_RESPONSE` | 服务端响应缺少稳定机器字段；检查反向代理是否返回 HTML 或错误版本 API。 |
| `MCPCAT_NETWORK_ERROR` | 检查 DNS、TLS、反向代理、`public_base_url` 和 15 秒请求超时。 |
| `MCPCAT_AGENT_AMBIGUOUS` | 设置默认 Agent，或显式传 `--agent` / `--all-detected-agents`。 |
| `MCPCAT_TARGET_INVALID` | 检查 Scope/目录；Generic 必须提供可写且非文件系统根目录的 `--target-dir`。 |
| `MCPCAT_INTEGRITY_ERROR` / `MCPCAT_PACKAGE_INVALID` | 不要绕过校验；让管理员检查 Registry 制品 SHA-256 与 ZIP。 |
| `MCPCAT_INSTALL_LOCK_BUSY` | 确认没有其他安装进程；仅在确认进程已退出后处理 `locks/` 中的残留锁。 |
| doctor 提示 mcporter 不可用 | 安装与 Skill 兼容的固定版本，并确认 `mcporter --version` 可从 PATH 运行。 |
| 更新失败 | 原安装保持独立；查看逐目标结果，修复网络/权限后重试或执行 rollback。 |

`--json` 返回稳定 envelope，适合 CI 判断错误码；CLI 会统一脱敏已知凭证。排障时
可以记录错误码、HTTP 状态和目标路径，但不得复制 API Key、OAuth Token 或完整
环境变量。
