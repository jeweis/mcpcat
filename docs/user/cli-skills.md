# 让 AI 使用 mcpcat CLI

仓库的 `skills/` 提供官方 Agent Skills，让 AI 将自然语言请求转换为 mcpcat CLI 操作。先把需要的 Skill 文件夹复制到 Agent 的 Skills 目录，再让 Agent 按任务使用；安装这些说明文件不会安装 CLI，也不会连接或修改远程实例。

## 选择 Skill

| Skill | 适合的请求 | 目录 |
| --- | --- | --- |
| `mcpcat-cli` | “连接公司实例，把 weather-tools 安装到 Codex 项目里” | [连接与本地安装](../../skills/mcpcat-cli/SKILL.md) |
| `mcpcat-servers` | “检查 time-demo 的状态，按这份配置更新服务” | [MCP 服务管理](../../skills/mcpcat-servers/SKILL.md) |
| `mcpcat-skills-manage` | “把这个 Skill 上传成草稿，供管理员审核发布” | [服务端 Skills 管理](../../skills/mcpcat-skills-manage/SKILL.md) |
| `mcpcat-apps` | “上传已构建的 HTML App，配置工具绑定” | [MCP Apps 管理](../../skills/mcpcat-apps/SKILL.md) |

四个目录都可独立复制，不依赖彼此或仓库中的其他文件。需要所有能力时复制四个目录；只需安装本地 Skills 时使用 `mcpcat-cli` 即可。它们是操作 CLI 的说明，与平台根据 MCP 工具生成的 Skills 不同。

## 安装给 Agent

获取本仓库后，在 **mcpcat 开源仓库根目录**（包含 `cli/`、`skills/` 的目录）执行。以下示例将四个 Skill 安装到用户级通用目录，适用于 Codex、Cursor、Pi 和 DSH：

```bash
(
set -e
mkdir -p "$HOME/.agents/skills"
for name in mcpcat-cli mcpcat-servers mcpcat-skills-manage mcpcat-apps; do
  if [ ! -f "skills/$name/SKILL.md" ]; then
    echo "找不到源 Skill，请进入 mcpcat 开源仓库根目录：$name"
    exit 1
  fi
  if [ -e "$HOME/.agents/skills/$name" ] || [ -L "$HOME/.agents/skills/$name" ]; then
    echo "目标已存在，请先核对并备份：$name"
    exit 1
  fi
done
for name in mcpcat-cli mcpcat-servers mcpcat-skills-manage mcpcat-apps; do
  cp -R "skills/$name" "$HOME/.agents/skills/$name"
done
)
```

Windows PowerShell 等价示例：

```powershell
$ErrorActionPreference = 'Stop'
$skillNames = @('mcpcat-cli', 'mcpcat-servers', 'mcpcat-skills-manage', 'mcpcat-apps')
$skillDestination = Join-Path $HOME '.agents/skills'
New-Item -ItemType Directory -Force -Path $skillDestination | Out-Null
foreach ($skillName in $skillNames) {
  if (-not (Test-Path -LiteralPath (Join-Path "skills/$skillName" 'SKILL.md') -PathType Leaf)) {
    throw "找不到源 Skill，请进入 mcpcat 开源仓库根目录：$skillName"
  }
  if (Test-Path -LiteralPath (Join-Path $skillDestination $skillName)) {
    throw "目标已存在，请先核对并备份：$skillName"
  }
}
foreach ($skillName in $skillNames) {
  Copy-Item -LiteralPath (Join-Path 'skills' $skillName) -Destination $skillDestination -Recurse
}
```

Claude Code 用户将目标换为 `~/.claude/skills`。项目级安装放到目标项目的 `.agents/skills`（Claude Code 为 `.claude/skills`），仅安装所需 Skill 文件夹，不把本仓库的 `AGENTS.md` 复制过去。其他 Agent 的目录见 [CLI 的 Agent 对照表](cli.md#2-选择-agent)。

目标结构应为 `<Skills目录>/mcpcat-cli/SKILL.md`，不要额外套一层 `skills/`。按 Agent 的加载方式重新加载 Skills 或开启新会话；支持显式调用的 Agent 可使用 `$mcpcat-cli` 等名称。随后说明目标实例、资源和期望操作。

这些文件尚不代表已上传到你的 mcpcat Registry，因此不能直接运行 `mcpcat skills install mcpcat-cli`。管理员可使用 `skills manage upload` 分别上传每个目录，完成审核发布后再通过该实例分发。后续更新手动安装副本前先备份并核对差异；不要覆盖自己的修改。

## CLI 和连接准备

需要 Node.js 24+。npm 安装与连接示例：

```bash
npm install --global @jeweis/mcpcat
mcpcat -V
mcpcat --help
mcpcat connect company https://mcpcat.example.com
```

由你在终端的隐藏输入提示中输入 API Key，不要粘贴到 AI 对话中。非交互环境通过安全凭证系统注入环境变量，见 [CLI 凭证与 CI](cli.md#凭证与-ci)。

`servers`、`skills manage`、`apps` 为当前源码新增命令，用户文档仍标记尚未发布到 npm。安装 npm 包不保证具备这些命令，先检查实际 `--help`。开发者可在受信任的仓库版本中构建 CLI：

```bash
cd cli
pnpm install --frozen-lockfile
pnpm build
npm install --global .
mcpcat servers --help
mcpcat skills manage --help
mcpcat apps --help
```

该操作会以本地构建替换全局 `mcpcat`，请保留所用版本信息；详细功能与兼容边界以 [CLI 用户文档](cli.md) 和实际帮助为准。Skill 会先检查能力，缺少命令时说明原因，不以其他接口绕过。

## 使用时说明结果目标

可以直接向 AI 提出：

- “使用 company 连接，将 weather-tools 的 1.2.0 版安装到当前项目的 Codex Skills 目录。”
- “查询 company 的 MCP 服务，告诉我 time-demo 的状态。”
- “把 meeting-planner 目录上传为 1.0.0、both 模式的草稿。”
- “将 clock.html 上传为 world-clock 1.0.0 草稿，先让我审核绑定和权限。”

查询不会自动授权重启、删除、发布或公开；明确的操作请求无需反复确认。服务更新会完整替换配置并重启；Apps 配置和绑定同样是完整替换。Apps 发布会设置整个 App 的可见性；添加 `--public` 会公开历史已发布版本，省略则会将整个 App 改为私有。公开发布需要明确范围及人工审核，`--yes`、`--reviewed` 不能代替授权或审核。写操作中断时 AI 应先核实状态，不能盲目重试。撤回不能收回客户端已下载的内容。

本地 Skill 的 `update/pin/unpin/rollback` 按安装记录筛选，不限制当前目录；`--scope project` 可能影响多个项目，`--target-dir` 不过滤这些操作。用户只要求一个项目时，AI 应说明限制，不擅自操作其他目录。`pin --version` 只固定已安装版本。`doctor` 的成功退出码也不代表环境健康，应看 `data.healthy/checks`。

## 维护这些 Skills

命令和参数以 `cli/src/cli.ts`、`args.ts`、`management-*.ts` 为事实来源；Agent 和路径以 `cli/src/agents/` 为准。修改 CLI 能力时同步维护相关 Skill 与 [CLI 用户文档](cli.md)。Skill 内示例使用占位资源与明确版本。除了检查帮助、参数解析和文件结构，还应运行隔离的端到端回归：

```bash
pnpm --dir cli build
.venv/bin/python -m pytest tests/test_cli_official_skills_e2e.py tests/test_cli_management_http.py -q
```

测试需要可绑定的随机回环端口。新增回归通过 Node 子进程运行正式 CLI 入口，只将 Profile、安装记录与 Agent 目录重定向到临时路径；HTTP 请求、认证、SQLite、制品校验和 MCP 服务生命周期使用真实实现。测试会原样上传、发布、下载四个官方 Skill，安装到隔离的 Codex 项目目录并校验字节，同时验证 Claude 用户级安装、固定/更新/回滚、MCP 服务配置与启停、App 工具绑定和跨版本可见性，以及缺少审核或确认、只读 Key 写入等失败情况。

这些测试验证说明中的工作流能够通过实际 CLI 执行，不替代各 AI 模型的自动触发评测，也不代表已在所有第三方 Agent 内激活或完成真实业务工具调用。测试不接触用户的 Keychain、现有 CLI 配置或业务实例；系统 Keychain 隐藏输入与 Windows 安装示例仍需对应平台验证。
