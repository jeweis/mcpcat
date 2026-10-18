# CLI 平台管理增量验收

2026-10-18，本地候选实现；尚未推送、公共发布或归档提案。版本号未改动，不代表 npm 同版本已具备管理命令。

## 范围与边界

对应 `upgrade-fastmcp-v4-and-official-skills` 第 19 节。CLI 源码仍位于开源仓库 `cli/`，新增 `servers`、`skills manage`、`apps`；原连接、Agent 与本地安装命令不改义。

不增加用户测试命令、协议检查器、App 渲染或内容执行。现有 `doctor` 保留兼容。上游 Apps 只查询，不复制为托管资源。业务 API、数据库 Schema 与旧 Registry 保持不变。

## 已验证证据

- Node.js 24：TypeScript 类型检查、ESLint、构建通过。
- Vitest：16 文件、129 项通过，包括原 76 项兼容回归。新增参数/路径/HTTP 映射、Profile + Keychain、固定实例前缀、无重定向/重试、确认、权限错误、脱敏、目录 ZIP、符号链接、下载摘要与拒绝覆盖等覆盖。
- `tests/test_cli_management_http.py`：已构建 CLI 通过随机回环端口连接真实 mcpcat API，使用临时 SQLite、Skills 与 Apps 制品目录；验证 READ 拒绝上传、Skill 双模式上传/发布/读取/下载/撤远程、旧 `skills list`、App 草稿/审核配置/工具绑定/发布/读取/原字节下载/撤回。
- 上述 CLI HTTP 与 `test_app_management_api.py`、`test_skill_content_workflow.py` 联合回归通过。
- 本地 npm 包元数据/制品/秘密检查通过；tarball 安装后原版本命令和三组新管理帮助通过。
- 发布隔离策略、Gate C 文档与兼容矩阵检查通过；历史公共发布/回滚演练状态未改，不把本地验收当成公共发布。
- OpenSpec strict 校验通过。

## 复验

```bash
cd cli
pnpm install --frozen-lockfile
pnpm typecheck
pnpm lint
pnpm test
pnpm build
node ../.github/scripts/cli-verify-package.mjs
node ../.github/scripts/cli-package-e2e.mjs
node ../.github/scripts/cli-verify-release-policy.mjs
node ../.github/scripts/cli-verify-gate-c-docs.mjs
cd ..
python -m pytest tests/test_cli_management_http.py tests/test_app_management_api.py tests/test_skill_content_workflow.py
```

Python 需项目 FastMCP 4.1 依赖；CLI HTTP 集成用例需 Node.js 与预先构建的 `cli/dist/bin.js`，未具备时独立后端回归会跳过该用例。测试只使用合成凭证，不连接已有 Docker。打包安装需要能获取 npm 依赖；整个流程不执行 `npm publish`。

## 用户入口

用法以 [CLI 用户文档](../user/cli.md) 和实际 `--help` 为准。管理写操作需确认，非交互 `--yes`；发布额外 `--reviewed`，公开额外 `--public`。配置文件完整替换，不从标准输出恢复秘密配置；超时后先查询状态，不盲目重试。
