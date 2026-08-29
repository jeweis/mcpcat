# 服务端、Registry、CLI 与 Skill 兼容矩阵

机器可读源为 [`compatibility-matrix.json`](./compatibility-matrix.json)，CI 使用
`.github/scripts/cli-verify-gate-c-docs.mjs` 将其与代码常量交叉验证。

## 独立版本维度

| 维度 | 当前值 | 兼容判定 |
| --- | --- | --- |
| mcpcat 服务应用 | `0.1.1` | 仅用于发布识别，不要求与 CLI 或 Skill 相等。 |
| Registry API | 服务端 `v1` / CLI `v1` | 必须精确相等，否则 CLI 拒绝。 |
| Registry Schema | 服务端 `1.0.0` / CLI major `1` | major 必须相同；minor/patch 可演进，但必填字段仍须通过解析。 |
| CLI | `0.1.0` | 服务端 minimum/recommended 均为 `0.1.0`；低于 minimum 时返回 HTTP 426。 |
| Skill | 独立 SemVer | 不与服务端/CLI 对齐；已发布版本不可变并验证 SHA-256/包结构。 |
| MCP Skill | generator `1.0.1`、Node `>=24`、mcporter `0.13.7` | 记录在制品兼容元数据；doctor 检查运行环境。 |

CLI 每次请求发送 `X-Mcpcat-CLI-Version`。Bootstrap 返回 API、Schema、minimum 和
recommended；Registry 索引再次返回 API 与 Schema。recommended 是非阻断提示，
服务应用版本变化本身也不触发拒绝。

## 场景矩阵

| 场景 | 结果 | 原因 |
| --- | --- | --- |
| API `v1`、Schema `1.0.0`、CLI `0.1.0` | allow | 当前基线。 |
| 服务应用 `0.2.0`，协商字段不变 | allow | 服务版本独立。 |
| Schema `1.9.0` 且必填字段兼容 | allow | 同 major，仍需严格解析。 |
| API `v2` | reject | CLI 只支持精确 `v1`。 |
| Schema `2.0.0` | reject | major 不兼容。 |
| CLI `0.0.9` | reject / HTTP 426 | 低于服务端 minimum。 |
| Skill `9.4.2` 配合 CLI `0.1.0` | conditional allow | Skill 版本独立，仍需自身兼容与完整性通过。 |

## 升级与回退规则

1. API 或 Schema major 变化必须先发布支持它的 CLI，并保留旧 API 迁移窗口。
2. 只新增可选字段可提升 Schema minor；删除、改名或改变必填语义提升 major。
3. 只在旧 CLI 会产生错误或不安全行为时提高 minimum；recommended 可先做提示。
4. Skill 内容变化创建新的不可变 SemVer。MCP Schema/生成器变化创建草稿，不自动发布。
5. 回退服务端时恢复匹配的 minimum/recommended；CLI dist-tag 与 Skill 回滚相互独立。

## 验证限制

本地校验确认代码常量、矩阵、README、工作流和演练状态一致；现有单元测试覆盖
API/Schema/minimum 拒绝。它不等于远程 HTTPS、npm beta、真实 Agent 或 Gate C
发布演练；完成外部步骤前，16.1、15.6 和 16.6 必须保持未完成。
