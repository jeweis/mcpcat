# 升级版本策略

先运行 [兼容矩阵的三个门禁](../../cli/docs/compatibility-matrix.md#升级发布门禁)，再执行明确授权的正式发布。本次验收后统一产品版本选择 `1.1.0`，后端、内置 Web、CLI 与 Docker 协同发布；不得覆盖已发布的 `1.0.1` npm 包或 Tag，也不提升最低 CLI 版本。

## 独立版本与不变量

- 应用、Python 包和内置 Web 产品版本保持一致；Docker `v*` Tag 必须匹配应用版本。下一次正式发布应选择新的 SemVer，不能复用已发布 `v1.0.1`。
- 新 CLI 以 `1.1.0` 发布，旧 CLI `1.0.1` 仍受支持：Registry `v1` / Schema `1.0.0` 不变，minimum 仍 `0.1.0`，recommended 为 `1.1.0`；旧版本真实兼容矩阵已经通过。
- 生成器 `1.1.0` 与 local/remote 模板各 `1.0.0` 分别记录；运行依赖和 MCP 协议、SQLite revision 均为其他维度，不能机械替换成产品版本。
- Skill/App 内容版本独立、已发布不可变。历史 local ZIP、bundle、上传文件和已审核 Apps 派生摘要不随依赖升级重写。
- Alembic 新 head 不保证旧程序可读取。恢复旧程序须恢复匹配的完整快照，不通过降低依赖版本或手改 `alembic_version` 伪装回退。

## 固定基线与发布顺序

机器来源是 [compatibility-matrix.json](../../cli/docs/compatibility-matrix.json)，运行依赖实际解析来自 `uv.lock`。源码固定 FastMCP/Prefab/Monty；MCP SDK 由锁文件固定，不额外虚构顶层依赖。官方 Apps Host bridge 在前端独立 `apps-host/package.json` 固定 `@modelcontextprotocol/ext-apps@2.0.3`，应通过既有 Web 构建同步进后端；后端独立仓库门禁不假定 CI 存在前端 sibling checkout。

发布前停写并保存升级前数据库、制品、配置及程序版本；隔离副本完成迁移、关闭新入口的兼容测试，再分别验证新 Skills、CodeMode、Apps。保留 ZIP/CLI 默认 local 和原 MCP/Catalog 路由。正式发布权限、npm/Docker 凭证和上线窗口另行授权，本次门禁只做静态与本地验证。

门禁测试通过不等于第三方 Host 支持 UI/Skills，也不替代浏览器、协议、数据恢复和已发布 CLI 的真实验收。整体提案进度以任务清单及对应证据为准，前置提案的外部发布任务不借此完成。
