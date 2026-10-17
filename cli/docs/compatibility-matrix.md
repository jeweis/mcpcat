# 服务端、Registry、CLI 与 Skill 兼容矩阵

机器可读源为 [`compatibility-matrix.json`](./compatibility-matrix.json)，CI 使用
`.github/scripts/cli-verify-gate-c-docs.mjs` 将其与代码常量交叉验证。

## 独立版本维度

| 维度 | 当前值 | 兼容判定 |
| --- | --- | --- |
| mcpcat 服务应用 | `1.0.1` | 本轮与 Docker/前端/CLI 使用协同基线；后续兼容判定不要求与 CLI 或 Skill 相等。 |
| Registry API | 服务端 `v1` / CLI `v1` | 必须精确相等，否则 CLI 拒绝。 |
| Registry Schema | 服务端 `1.0.0` / CLI major `1` | major 必须相同；minor/patch 可演进，但必填字段仍须通过解析。 |
| CLI | `1.0.1` | 服务端 minimum 为 `0.1.0`、recommended 为 `1.0.1`；低于 minimum 时返回 HTTP 426。 |
| Skill | 独立 SemVer | 不与服务端/CLI 对齐；已发布版本不可变并验证 SHA-256/包结构。 |
| MCP Skill | generator `1.1.0`；local 要求 Node `>=24`、mcporter `0.13.7` | 生成器独立版本；remote 使用来源连接，不额外要求 Node/mcporter。已发布历史制品不变。 |
| 运行依赖 | FastMCP `4.1.0`、MCP SDK `2.3.0`、Prefab `0.20.2`、Monty `1.1.0` | 与 `uv.lock` 一致；不与产品版本强行对齐。 |
| Python | 最低 `>=3.10`，容器 `3.12`；实际验证 `3.10.19` / `3.11.14` | 最低版本定向矩阵与完整回归分开记录，不冒充 Docker/全平台认证。 |
| MCP 协议 | 现代 `2026-07-28`；旧 HTTP `2025-03-26` / `2025-06-18` / `2025-11-25` | 由框架协商；协议兼容不代表 Host 会激活 Skills 或渲染 Apps。 |
| SQLite Alembic | 原基线 `0002_skills_registry` → `0005_app_render_review` | 与 Registry JSON Schema 不同；回退需要匹配的数据库、制品和配置快照。 |
| 内容模板 | local / remote 各 `1.0.0` | 与生成器独立；模板语义变化须更新对应版本并创建新草稿。 |
| HTML 派生入口 | 新草稿 `html-assembly-5`，保留 `1`–`5` | 读取按冻结版本装配，不能以默认版本重新生成历史入口。 |

CLI 每次请求发送 `X-Mcpcat-CLI-Version`。Bootstrap 返回 API、Schema、minimum 和
recommended；Registry 索引再次返回 API 与 Schema。recommended 是非阻断提示，
服务应用版本变化本身也不触发拒绝。

## 场景矩阵

| 场景 | 结果 | 原因 |
| --- | --- | --- |
| API `v1`、Schema `1.0.0`、CLI `1.0.1` | allow | 当前协同发布基线。 |
| 服务应用 `1.1.0`，协商字段不变 | allow | 基线之后服务版本可独立演进。 |
| Schema `1.9.0` 且必填字段兼容 | allow | 同 major，仍需严格解析。 |
| API `v2` | reject | CLI 只支持精确 `v1`。 |
| Schema `2.0.0` | reject | major 不兼容。 |
| CLI `0.0.9` | reject / HTTP 426 | 低于服务端 minimum。 |
| Skill `9.4.2` 配合 CLI `1.0.1` | conditional allow | Skill 版本独立，仍需自身兼容与完整性通过。 |

## 升级与回退规则

1. API 或 Schema major 变化必须先发布支持它的 CLI，并保留旧 API 迁移窗口。
2. 只新增可选字段可提升 Schema minor；删除、改名或改变必填语义提升 major。
3. 只在旧 CLI 会产生错误或不安全行为时提高 minimum；recommended 可先做提示。
4. Skill 内容变化创建新的不可变 SemVer。MCP Schema/生成器变化创建草稿，不自动发布。
5. 回退服务端时恢复匹配的 minimum/recommended；CLI dist-tag 与 Skill 回滚相互独立。

## 升级发布门禁

`verify-upgrade-compatibility.mjs` 静态核对 Python 最低/容器版本、锁文件、顶层固定依赖、Skills 协议标识、Prefab renderer、local/remote 模板、HTML 历史装配分支及单一 Alembic head；由现有 CLI 文档门禁和 Docker 版本门禁调用。14 项 Node 测试包含 13 种故意漂移，确认不一致会拒绝而不是自动改版本。

```bash
node .github/scripts/cli-verify-gate-c-docs.mjs
node .github/scripts/verify-service-release-version.mjs
node --test .github/scripts/verify-upgrade-compatibility.test.mjs
```

这些命令不发布 npm/Docker，不改写历史 ZIP，也不将 Alembic head 等同于 Registry Schema。完整维度、发布顺序和实际验收边界见 [升级版本策略](../../docs/maintainer/upgrade-version-policy.md)。

## 验证限制

本地校验确认代码常量、矩阵、README、工作流和演练状态一致；现有单元测试覆盖
API/Schema/minimum 拒绝。它不等于远程 HTTPS、npm beta、真实 Agent 或 Gate C
发布演练；完成外部步骤前，16.1、15.6 和 16.6 必须保持未完成。
