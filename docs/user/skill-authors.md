# 编写与发布 Skill

上传标准 Skill ZIP，或在 MCP 服务/工具搜索 Catalog 中生成新 Skill，审核后选择本地安装、远程读取或两种方式。mcpcat 是存储、版本和分发中心，不是上传脚本的执行环境。本页针对未发布的 FastMCP 4.1 升级代码。

## 最小内容

ZIP 内使用一个 Skill 根目录，包含 `SKILL.md`。例如：

```text
report-review/
  SKILL.md
  references/checklist.md
  scripts/check.py
```

```markdown
---
name: report-review
description: 当需要检查报告中的缺失信息、核对引用并整理修改建议时使用。
compatibility: 如需运行检查脚本，客户 Agent 环境需提供 Python。
---
# 检查报告

先确认报告与检查范围，按 references/checklist.md 核对内容。
只在客户环境具备运行条件且获得用户授权后使用 scripts/check.py。
保留不确定项，不自动修改或发送原报告。
```

`name` 与根目录名称一致。description 写用途与触发场景；运行时、连接方式和依赖放在 compatibility/正文。长资料放 references，正文只给必要流程和相对链接。不要把真实 Key、Token、私人配置或凭证放进文件。

包接受脚本和附件，但校验路径、格式、文件及大小，不执行、导入或安装依赖。远程发布当前限制为最多 512 个文件、16 MiB；超限旧 local 制品不因此被自动改写或发布 remote。前端文本预览限制与正式 Resources 下载限制不同，二进制仍按原字节提供。

## 选择交付方式

普通上传 Skill 若指令相同，可让 local/remote 共用同一不可变包；不强制保存两套文件。上传不会把已有 mcporter 指令自动翻译成另一套远程调用说明。

- local：客户通过 ZIP/[CLI](cli.md) 安装；所需脚本由客户 Agent 环境执行。
- remote：支持官方 Skills 扩展的 Host 发现指令、按清单与 SHA 按需取文件；脚本仍须客户取得并本地执行。只读指令型 Host 不能替代脚本运行环境。
- 独立 Skills 端点：只绑定不依赖该端点 MCP 工具的工作流/参考/本地脚本；不凭空提供其他服务的工具。
- 服务端点：指令需要该服务实际工具时明确绑定它；跨服务搜索流程绑定 Catalog，并使用 search_tools/call_tool。来源不可用时不擅自换成同名工具。

MCP 生成器提供两种可审阅模板，仓库路径 `app/resources/skill_templates/`。local 保留固定 mcporter 0.13.7、Node 24+、bundled config 和环境凭证占位符；remote 使用来源 Host 的 MCP 工具和实时 Schema，不为工具调用额外要求 mcporter。两种 description 共享用途/触发语义。

## 审核、更新与回退

上传/生成先创建草稿，逐文件检查、确认端点适用性、运行前提和公开范围后发布完整版本。已发布版本的文件、变体集合与绑定被冻结；新增远程方式、修改指令或 CSP 不能覆盖旧版本，应创建更高 SemVer 并重新审核。

local latest 与 remote latest 分开选择，remote-only 新版不会推动旧 CLI 更新。历史 ZIP、SHA、bundle 和 pin 保持原语义。归档/撤销会拒绝服务器的新读取，但不能抹除客户已经安装或缓存的文件；不要把撤销当作删除客户文件的保证。

接入步骤及实际客户端矩阵见 [远程 Skills](remote-skills.md)。连接成功或 Resources 可读不等于 Agent 自动激活；目前未验证激活的 Agent 继续用 ZIP/CLI。
