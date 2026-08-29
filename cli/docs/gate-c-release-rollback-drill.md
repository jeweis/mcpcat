# Gate C 发布与回滚演练记录

状态：**未完成（仅完成本地 dry-run）**

没有 npm Trusted Publisher、受保护 GitHub Environment、真实 beta、远程 HTTPS
实例及真实 Agent 证据时，不得把本地结果写成 Gate C 发布演练完成。

## 已完成的本地 dry-run

日期：2026-08-29

| 检查 | 真实本地结果 | 边界 |
| --- | --- | --- |
| 工具链 | PASS：Node `25.6.1`（满足 >=24）、npm `11.9.0`、pnpm `11.19.0` | 不是 GitHub-hosted Node 24。 |
| lint/typecheck/build | PASS | 本地工作树。 |
| CLI tests | PASS：15 files / 76 tests | 含本地多 Agent/Scope Gate C E2E，不含远程 HTTPS。 |
| npm pack/包检查 | PASS：`@jeweis/mcpcat@1.0.1`，123 files，README/LICENSE/repository/bin/秘密扫描通过 | 未上传 npm。 |
| tarball 冒烟 | PASS：本地安装后 `mcpcat --version --json` 返回 `1.0.1` | 不是已发布包。 |
| release policy | PASS：main 无发布能力；stable/beta/next 和 OIDC/provenance 静态检查通过 | 未换取真实 OIDC。 |
| 兼容矩阵/文档 | 本轮脚本验证后记录 | 仅静态校验。 |

以上未执行 `npm publish`、`npm dist-tag`、GitHub Release、账号配置或真实 Agent，
不产生外部状态。

## 全量回归与安全审查

日期：2026-08-29

- 后端全量回归：99 项通过，覆盖 SQLite/Alembic、legacy JSON 迁移、现有 MCP
  业务、Skills Registry、恶意 ZIP、生成、分发与制品完整性。
- Flutter 全量回归：20 项通过；`flutter analyze` 保留 70 条项目既有
  warning/info 作为独立质量债务。
- CLI：lint、typecheck、build 通过，15 个测试文件共 76 项通过；本地多 Agent
  E2E 覆盖 2 Profile × 3 Agent × 2 Scope 的独立生命周期。
- 发布校验：包内容、README、MIT LICENSE、repository/bin、秘密扫描、兼容矩阵和
  main 分支发布隔离均通过。审查中发现的 README 缺失问题已修复并复验。
- 安全审查未发现剩余高/中风险阻塞项；测试中的假凭证只用于脱敏断言。

此节完成 OpenSpec 16.5，但不代表完成真实发布/回滚演练。

## 正式发布演练模板

- 演练 ID、日期、操作者/复核者：`<填写>`
- 完整 Git SHA、CLI version/tag：`<填写>`
- npm package/dist-tag：`@jeweis/mcpcat` / `<beta|next|latest>`
- Actions run、npm provenance、远程 HTTPS URL：`<填写，不含凭证>`

### 前置条件

- [ ] npm Trusted Publisher 指向 `jeweis/mcpcat`、`cli-publish.yml`、`npm` Environment。
- [ ] GitHub `npm` Environment 受保护且限制 `v*` tag。
- [ ] CI、包检查、秘密扫描和兼容矩阵通过。
- [ ] 已记录发布前 beta/next/latest 和上一良好版本。
- [ ] 测试凭证只通过安全环境注入，日志无秘密。

### 步骤与证据

1. 创建与服务端及 `package.json` 完全匹配的统一 `v<version>` tag。
2. beta/next Release 标记 prerelease；稳定版不得带预发布标识。
3. 记录保护审批、OIDC job、provenance、tarball SHA/integrity。
4. 从干净环境安装 npm 版本并确认 `mcpcat --version --json`。
5. 对远程 HTTPS 执行 connect/list/install/update/pin/rollback/doctor。
6. 在全部受支持 Agent 的 user/project Scope 验证目录契约；共享 `.agents` 的 Agent
   验证共享版本语义，其他目录验证独立生命周期。

| 步骤 | 命令/Action | 预期 | 实际 | 证据/复核 |
| --- | --- | --- | --- | --- |
| 发布 beta | `<填写>` | OIDC + provenance | `<填写>` | `<填写>` |
| 远程全流程 | `<填写>` | 全通过 | `<填写>` | `<填写>` |
| Agent 矩阵 | `<填写>` | 路径、共享/独立生命周期符合契约 | `<填写>` | `<填写>` |

## 正式回滚演练模板

1. 停止推广并保存 Actions/npm/CLI 脱敏证据。
2. 由有权限的 npm 操作者把 dist-tag 指回上一良好版本；OIDC publish 不代表已具备
   `npm dist-tag` 权限。
3. 若不移动 dist-tag，发布经过完整门禁的修复版本；不得用 `npm unpublish` 代替常规回滚。
4. 从干净环境重装并验证 CLI 版本。
5. Skill 不随 CLI dist-tag 自动变化；逐 Profile/Agent/Scope 执行
   `mcpcat skills rollback <slug> [--version <semver>]`。
6. 恢复服务端 minimum/recommended 到真实支持范围。

| 对象 | 回滚前 | 良好版本 | 操作 | 实际/证据 |
| --- | --- | --- | --- | --- |
| npm dist-tag | `<填写>` | `<填写>` | `<填写>` | `<填写>` |
| 远程兼容范围 | `<填写>` | `<填写>` | `<填写>` | `<填写>` |
| Agent Skill | `<填写>` | `<填写>` | `<填写>` | `<填写>` |

## 完成判定

- [ ] 真实 GitHub-hosted OIDC/provenance 发布成功。
- [ ] 真实 beta 与远程 HTTPS 全命令链通过。
- [ ] 三个 Agent user/project 生命周期通过。
- [ ] npm 与 Skill 回滚都已实际演练。
- [ ] 操作者和独立复核者签字，证据完整且无秘密。

当前结论：**Gate C 发布与回滚演练未完成，16.6 不得勾选。**

## 1.0.1 正式发布记录

日期：2026-08-29

本次已完成正式稳定版发布，但不替代上文尚未完成的 beta、远程 HTTPS 全命令链和
回滚演练：

- 发布提交：`0952460cd049816a635901deabed0f665326d795`
- 统一 Tag：`v1.0.1`
- CLI CI：GitHub Actions run `33238111811`，成功
- npm OIDC/provenance 发布：GitHub Actions run `33238144969`，成功
- npm：`@jeweis/mcpcat@1.0.1`，`latest=1.0.1`
- npm integrity：`sha512-JkRraLDigA65XAwjyi1NV3PsaK1H7VqYZDqxDJMMBhTF4CZhBJUBqaawAnQLjKhxDLE+mJd5uILH8kAYEWBMIw==`
- Docker Tag 发布：GitHub Actions run `33238144964`，成功
- Docker：`jeweis/mcpcat:1.0.1`、`:1.0`、`:latest`
- Docker OCI index digest：`sha256:82503095e38cdfecd5ee8f6883ab655de160586829b207ffc5d9cc083abc144f`
- Docker 平台：`linux/amd64`、`linux/arm64`，并包含对应 attestation manifest

npm Environment 的 Tag 保护规则已从旧的 `cli-v*.*.*` 迁移为统一的
`v*.*.*`；required reviewer 保护保持启用。本次证据仅完成 1.0.1 正式发布，不将
15.6、16.1 或 16.6 标记为完成。
