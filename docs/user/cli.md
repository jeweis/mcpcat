# mcpcat CLI：安装与使用

mcpcat CLI 用于连接远程 mcpcat 实例，并把实例中已发布的 Agent Skills 安装到受支持的编码 Agent 或自定义目录。npm 包名是 `@jeweis/mcpcat`，安装后的命令是 `mcpcat`。

## 环境要求

- Node.js 24 或更高版本。
- 一个可访问的 mcpcat 实例地址。
- 具备 read 权限的 mcpcat API Key；发布和管理 Skill 仍需在管理界面使用 write 权限。
- 安装 MCP 生成的 Skill 后，Agent 执行工具时需要网络访问和兼容的 mcporter。

## 安装

使用 npm 全局安装：

```bash
npm install --global @jeweis/mcpcat
mcpcat --version
```

升级到 npm 上的最新版本：

```bash
npm install --global @jeweis/mcpcat@latest
```

## 快速开始

### 1. 连接 mcpcat

只填写实例根地址，不要追加 `/api`：

```bash
mcpcat connect https://mcpcat.example.com
```

命令会发现实例的 Skills Registry，并以隐藏输入的方式读取 API Key。首个连接会保存为默认 Profile。连接多个实例时可以指定名称：

```bash
mcpcat connect company https://company.example.com
mcpcat connect personal https://personal.example.com
mcpcat profiles
mcpcat use company
```

默认只允许 HTTPS。`localhost` 和回环地址可以使用 HTTP；其他 HTTP 地址必须显式确认风险：

```bash
mcpcat connect http://mcpcat.lan:8000 --allow-http
```

### 2. 选择 Agent

查看和检测支持的 Agent：

```bash
mcpcat agents list
mcpcat agents detect
mcpcat agents use codex
```

支持的 Agent ID：

| Agent | ID | user Scope | project Scope |
| --- | --- | --- | --- |
| Codex | `codex` | `~/.agents/skills` | `<项目>/.agents/skills` |
| Claude Code | `claude` | `~/.claude/skills` | `<项目>/.claude/skills` |
| OpenClaw | `openclaw` | `~/.openclaw/skills` | `<项目>/skills` |
| WorkBuddy | `workbuddy` | `~/.workbuddy/skills` | `<项目>/.workbuddy/skills` |
| CodeBuddy | `codebuddy` | `~/.codebuddy/skills` | `<项目>/.codebuddy/skills` |
| Qoder | `qoder` | `~/.qoder/skills` | `<项目>/.qoder/skills` |
| Pi | `pi` | `~/.agents/skills` | `<项目>/.agents/skills` |
| DeepSeek Harness（DSH） | `dsh` | `~/.agents/skills` | `<项目>/.agents/skills` |
| Cursor | `cursor` | `~/.agents/skills` | `<项目>/.agents/skills` |
| 自定义目录 | `generic` | 必须指定 `--target-dir` | 必须指定 `--target-dir` |

检测到多个 Agent 且尚未设置默认 Agent 时，交互模式会要求选择。非交互环境必须传入 `--agent` 或 `--all-detected-agents`。

Codex、Pi、DeepSeek Harness 和 Cursor 共享通用 `.agents/skills` 目录；同一 Scope 下安装的同名 Skill 是同一份物理文件，不能分别固定为不同版本。由于 `.agents` 本身不能证明具体安装了哪个 Agent，CLI 对这些 Agent 使用对应命令进行自动检测。

### 3. 查找并安装 Skill

```bash
mcpcat skills list
mcpcat skills info weather-tools
mcpcat skills install weather-tools --agent codex --scope user
```

安装指定版本：

```bash
mcpcat skills info weather-tools --version 1.2.0
mcpcat skills install weather-tools --version 1.2.0 --agent codex
```

安装到项目级目录时，请先进入目标项目：

```bash
cd /path/to/project
mcpcat skills install weather-tools --agent codex --scope project
```

同一个 Skill 可以一次安装到多个 Agent：

```bash
mcpcat skills install weather-tools \
  --agent codex \
  --agent claude \
  --scope user

mcpcat skills install weather-tools --all-detected-agents
```

自定义目录必须使用 Generic Agent：

```bash
mcpcat skills install weather-tools \
  --agent generic \
  --target-dir /absolute/path/to/skills
```

CLI 会验证下载文件的 SHA-256 和 Agent Skill 结构，备份已有目录并原子替换。不同实际目录的安装生命周期相互独立；共享 `.agents/skills` 的 Agent 共用实际 Skill 版本。

## 更新、固定与回滚

```bash
# 更新一个 Skill
mcpcat skills update weather-tools

# 更新当前 Profile 下所有未固定的 Skill
mcpcat skills update --all

# 固定当前版本，或固定到明确版本
mcpcat skills pin weather-tools
mcpcat skills pin weather-tools --version 1.2.0

# 解除固定
mcpcat skills unpin weather-tools

# 回滚到最近本地备份，或指定 Registry 历史版本
mcpcat skills rollback weather-tools
mcpcat skills rollback weather-tools --version 1.1.0
```

`update --all` 会跳过已固定的安装。未指定版本的回滚优先使用最近的本地备份；指定版本时，CLI 会在需要时下载 Registry 中仍保留的历史版本。

## 支持的命令

| 命令 | 用途 |
| --- | --- |
| `mcpcat connect [profile] <url>` | 连接实例、验证凭证并保存 Profile。 |
| `mcpcat profiles` | 列出已保存的 Profile。 |
| `mcpcat use <profile>` | 设置默认 Profile。 |
| `mcpcat agents detect` | 检测本机可用的 Agent。 |
| `mcpcat agents list` | 列出 CLI 支持的 Agent。 |
| `mcpcat agents use <agent>` | 设置默认 Agent。 |
| `mcpcat skills list` | 列出当前凭证可见的已发布 Skills。 |
| `mcpcat skills info <slug>` | 查看 Skill 与版本信息。 |
| `mcpcat skills install <slug>` | 校验并安装 Skill。 |
| `mcpcat skills update <slug>` | 更新指定 Skill。 |
| `mcpcat skills update --all` | 更新当前 Profile 下所有未固定安装。 |
| `mcpcat skills pin <slug>` | 固定安装版本。 |
| `mcpcat skills unpin <slug>` | 解除版本固定。 |
| `mcpcat skills rollback <slug>` | 回滚指定 Skill。 |
| `mcpcat doctor` | 检查连接、凭证、Agent、安装记录、Node.js 和 mcporter。 |
| `mcpcat --help` | 显示当前版本支持的完整用法。 |
| `mcpcat --version` | 显示 CLI 版本。 |

## 常用选项

| 选项 | 说明 |
| --- | --- |
| `--profile <name>` | 仅为当前命令覆盖默认 Profile。 |
| `--json` | 输出适合脚本处理的稳定 JSON envelope。 |
| `--non-interactive` | 禁止交互输入，适合 CI。 |
| `--allow-http` | 允许连接非 localhost 的明文 HTTP 地址。 |
| `--agent <id>` | 指定安装目标 Agent，可以重复。 |
| `--all-detected-agents` | 安装到全部已检测 Agent。 |
| `--scope user\|project` | 选择用户级或项目级目录，默认是 `user`。 |
| `--target-dir <path>` | 覆盖安装目录；Generic Agent 必填。 |
| `--version <semver>` | 查看、安装、固定或回滚到明确版本。 |
| `--all` | 与 `skills update` 配合，更新全部安装。 |

## 凭证与 CI

交互式 `connect` 优先将 API Key 保存到系统 Keychain。普通 Profile 文件不会保存 API Key；Keychain 不可用时，CLI 不会把凭证降级写入明文文件。

CI 或其他非交互环境使用环境变量：

```bash
export MCPCAT_URL='https://mcpcat.example.com'
export MCPCAT_API_KEY='<由 CI secret 注入>'

mcpcat skills list --json --non-interactive
mcpcat doctor --json --non-interactive
```

`MCPCAT_URL` 与 `MCPCAT_API_KEY` 会临时覆盖本地 Profile。不要把 API Key 放进命令参数、日志、仓库或普通配置文件。

## MCP Skill 与 mcporter

mcpcat CLI 负责 Skill 的安装和生命周期管理，不替代 mcporter。MCP 生成的 Skill 通过包内 `config/mcporter.json` 连接远程 mcpcat。可以先运行诊断：

```bash
mcpcat doctor
```

如果诊断提示 mcporter 不可用，请按 Skill 的兼容信息安装对应版本。运行 Agent 前，通过环境或安全凭证系统提供 `MCPCAT_API_KEY`；只有需要覆盖 Skill 默认实例地址时才设置 `MCPCAT_URL`。

## 排障

优先运行：

```bash
mcpcat doctor --json
```

常见问题：

- 连接失败：检查 DNS、TLS、反向代理和实例根地址，不要在地址后追加 `/api`。
- 认证失败：重新执行 `connect`，检查 Keychain；CI 中同时设置 URL 和 API Key。
- 找到多个 Agent：执行 `mcpcat agents use <agent>`，或在命令中明确指定 `--agent`。
- 目标目录无效：确认目录可写；Generic Agent 必须指定非文件系统根目录的绝对路径。
- 完整性校验失败：不要绕过校验，请让管理员检查 Registry 制品。
- 更新失败：原安装会保留；修复网络或权限后重试，也可以执行 `skills rollback`。

如需自动化处理错误，请结合 `--json` 读取稳定错误码，输出日志前仍需避免复制任何凭证或完整环境变量。
