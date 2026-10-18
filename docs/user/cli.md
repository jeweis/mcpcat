# mcpcat CLI：安装与使用

mcpcat CLI 是远程 mcpcat 的命令行管理入口，同时保留把已发布 Agent Skills 安装到本地 Agent 的能力。npm 包名是 `@jeweis/mcpcat`，安装后的命令是 `mcpcat`。

> 本文新增的 `servers`、`skills manage`、`apps` 命令为当前源码能力，尚未发布到 npm。验收期间请使用仓库构建包；安装 npm 已发布版不代表已获得这些新命令。

## 环境要求

- Node.js 24 或更高版本。
- 一个可访问的 mcpcat 实例地址。
- 查询和安装使用 read 权限的 mcpcat API Key；上传、修改、发布与删除需要 write 权限，服务端仍负责权限校验。
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

## 平台管理（源码候选版）

这些命令复用当前 Profile、认证头和凭证，不需要第二套连接。全局支持 `--profile <名称>`、`--json`、`--non-interactive`、`--allow-http`。实例部署在反向代理子路径时，请保留实例前缀，但不要追加 `/api`。

写操作在交互终端要求输入 `yes`；自动化或 CI 必须显式 `--yes`。发布还需先人工审核内容，并传 `--reviewed`。公开访问另需 `--public`，不指定则按私有范围发布。CLI 不绕过服务端权限或不可变版本限制。

### MCP 服务

```bash
mcpcat servers list
mcpcat servers info time-demo
mcpcat servers config time-demo --output ./time-config.json
mcpcat servers add my-time --file ./new-time-config.json --yes
mcpcat servers update my-time --file ./new-time-config.json --yes
mcpcat servers stop my-time --yes
mcpcat servers start my-time --yes
mcpcat servers restart my-time --yes
mcpcat servers delete my-time --yes
```

配置文件直接填写 MCP 配置对象，不加 `name` / `config` 外壳。例如远程服务：

```json
{
  "type": "streamable-http",
  "url": "https://mcp.example.com/mcp",
  "require_auth": true,
  "enabled": true
}
```

`update` **完整替换配置并重启**，不是字段合并。`add/update` 可能在服务器启动用户配置的程序，需审核配置；启停、重启和删除可能中断连接。删除后不自动恢复。

标准输出隐藏整个 `config`、`env`、`headers`、`command`、`args`、OAuth 和凭证容器，避免任意字段绕过脱敏。查看配置使用显式 `--output`；导出文件为可再次提交的纯配置对象，可能含秘密，请妥善保存，不要提交 Git。只读 Key 得到的是服务端裁剪配置，不应直接拿来覆盖完整配置。导出只创建新文件，拒绝覆盖和符号链接；POSIX 权限为 `0600`，Windows 还应确认目录 ACL。

### 服务端 Skills 与本地安装的区别

`skills install/update/pin/unpin/rollback` 仍操作客户端本地安装；`skills manage …` 操作远程 mcpcat 的资源与版本，两者不混用。

```bash
mcpcat skills manage list
mcpcat skills manage info meeting-planner
mcpcat skills manage connections meeting-planner
mcpcat skills manage upload --file ./meeting-planner --version 1.0.0 --mode both --yes
mcpcat skills manage upload --file ./meeting-planner.zip --version 1.1.0 --mode remote --binding-kind service --source time-demo --yes
mcpcat skills manage generate time-demo --mode both --yes
mcpcat skills manage generate catalog --mode local --yes
mcpcat skills manage file meeting-planner --version 1.0.0 --path meeting-planner/SKILL.md
mcpcat skills manage download meeting-planner --version 1.0.0 --output ./meeting-planner-1.0.0.zip
mcpcat skills manage publish meeting-planner --version 1.0.0 --reviewed --yes
mcpcat skills manage withdraw-remote meeting-planner --version 1.0.0 --yes
mcpcat skills manage deprecate meeting-planner --yes
mcpcat skills manage archive meeting-planner --yes
mcpcat skills manage delete unpublished-draft --yes
```

- 上传和生成**只产生草稿**，不会自动发布。相同生成快照可能返回 `unchanged`；生成较慢时请等命令结束，不要重复提交。
- `--mode` 默认 `local`；上传和生成另支持 `remote`、`both`。本地安装保持原默认行为。
- 上传绑定：默认 `independent`；`service` 必须带 `--source <服务名>`；`catalog` 绑定平台工具搜索入口，不带 `--source`。生成时 `catalog` 是平台入口保留参数。
- 上传可带 `--changelog <说明>`。目录根名称应与 `SKILL.md` 的 `name` 一致，采用小写字母/数字/连字符。
- 目录在本机打包，不执行任何文件。拒绝符号链接，默认排除 `.git`、`node_modules`、`__pycache__`、`.venv`、`.ssh`、`.DS_Store`、`.env` / `.env.*`、`.pem` / `.key` / `.pyc`；排除清单会回显。其他秘密仍需用户自行审核。原 ZIP 字节不改写，也不会替你过滤其秘密文件。
- 目录限 512 文件、总内容 16 MiB、单文件 10 MiB、深度 16 层、扫描 4096 项；ZIP 上传限 20 MiB，最终仍由服务端校验。
- `info` 返回版本/变体和文件清单；`--path` 使用清单中的完整路径（通常含 Skill 根目录）。`file/download` 支持 `--mode local|remote` 和 `--binding <绑定标识>`，绑定标识从 `info/connections` 获取，不猜测。
- `publish --public` 允许远程匿名读取审核后的 Skill；是否可用仍受资源/来源权限限制。`withdraw-remote` 只撤远程变体，可带 `--binding`，不删除客户端已安装内容。
- 退役/归档不等于本地卸载；删除仅适用于服务端允许删除的未发布资源，已发布版本不能靠删除命令覆盖。

### MCP Apps

```bash
mcpcat apps list
mcpcat apps list --source time-demo
mcpcat apps info open_world_clock --source time-demo
mcpcat apps upload world-clock --file ./clock.html --version 1.0.0 --display-name "世界时钟" --yes
mcpcat apps info world-clock
mcpcat apps config world-clock --version 1.0.0 --file ./review.json --yes
mcpcat apps bindings world-clock --version 1.0.0 --file ./bindings.json --yes
mcpcat apps publish world-clock --version 1.0.0 --reviewed --yes
mcpcat apps connections world-clock
mcpcat apps file world-clock --version 1.0.0 --path index.html
mcpcat apps download world-clock --version 1.0.0 --output ./world-clock.html
mcpcat apps withdraw world-clock --version 1.0.0 --yes
```

`apps list` 分开列出托管资源和各 MCP 来源应用，不按同名合并；来源失败或发现被截断时 `complete=false`，保留其他成功结果，不表示“没有应用”。`--source` 只用于 `list/info`；来源详情标识是入口工具名，托管详情标识是 slug。上游只发现和查询，不上传副本、不发布、不执行、不渲染。

托管 App 上传接受已构建 `.html`、Prefab `.json` 或 `.zip`，限 16 MiB，不运行构建脚本。ZIP 默认入口为 `index.html`，可用 `--entrypoint <包内路径>` 指定；单个 HTML / JSON 的包内名称分别为 `index.html` / `app.json`。

`review.json` 示例（不需要外部域名或设备权限时）：

```json
{"csp": {}, "permissions": {}, "changelog": "已审核应用内容"}
```

`bindings.json` 示例（显式授权工具别名，不是上传服务端脚本）：

```json
{"bindings": [{"alias": "current_time", "server_name": "time-demo", "tool_name": "get_current_time"}]}
```

`config/bindings` 分别**完整替换**草稿的审核配置和工具绑定，空 `bindings` 数组会移除全部授权。发布后版本不可覆盖。`publish --public` 的公开范围是该 App 下已发布版本，不仅本次版本；请确认范围再执行。`withdraw` 撤回指定版本但保留制品，不承诺撤回客户端已下载内容。没有虚构 `apps delete/test/inspect` 等命令。

### 下载、失败与脚本使用

所有版本操作要求明确 SemVer，不接受 `latest`。文件预览有服务端限额；二进制或过大文件返回不可预览原因，应下载完整制品。下载限 50 MiB，校验服务端 SHA-256，必须指定 `--output` 新文件，不自动安装、解压或执行；摘要错误不会保存制品。

管理请求不跟随重定向，不自动重试。写入遇到超时、连接中断、无效响应时，**结果可能已经提交**：先用 `info/list` 或管理页面核实，不要盲目重试。HTTP 错误只提供状态码，不回显可能含秘密的上游正文。旧版服务端没有新增 API 时会明确报错或标明来源发现不完整，不伪装成功。

管理命令支持 `--json`，沿用原 `{ok,data}` / `{ok:false,error}` envelope。服务端返回业务数据保留在 `data` 内；上传 Skill 的回显为 `data.result` 与 `data.excluded`。Apps 列表为 `data.hosted`、`data.sources`、`data.complete`。自动化程序应检查退出码与 envelope，Apps 部分结果还需检查 `complete`。

查看完整新增命令帮助：

```bash
mcpcat servers --help
mcpcat skills manage --help
mcpcat apps --help
```

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
