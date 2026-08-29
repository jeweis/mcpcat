# mcpcat CLI 发布说明

CLI 位于后端开源仓库的 `cli/` 目录。CLI 仍保留独立兼容边界，但正式协同发布使用
仓库统一的 `v<version>` Tag。npm 包名为 `@jeweis/mcpcat`，安装后的命令始终为
`mcpcat`。

## 发布通道

版本和 npm dist-tag 使用固定映射，其他预发布标识会被工作流拒绝：

| CLI 版本 | Git tag | npm dist-tag | GitHub Release |
| --- | --- | --- | --- |
| `1.2.3-beta.1` | `v1.2.3-beta.1` | `beta` | 标记为 prerelease |
| `1.2.3-next.1` | `v1.2.3-next.1` | `next` | 标记为 prerelease |
| `1.2.3` | `v1.2.3` | `latest` | 正式 Release |

发布只接受与 `cli/package.json` 版本完全一致的 `v<version>` 标签。普通
`main`/`master` push 和 Pull Request 只运行 `cli-ci.yml`，该工作流没有 OIDC
写权限，也不包含任何 npm 发布命令。

## 一次性外部配置

这些设置不能通过仓库文件代替，首次发布前必须由仓库/npm 管理员完成：

1. 在 npm 的 `@jeweis/mcpcat` 包设置中添加 GitHub Actions Trusted Publisher：
   - repository：`jeweis/mcpcat`
   - workflow：`cli-publish.yml`
   - environment：`npm`
2. 在 GitHub 仓库创建名为 `npm` 的 Environment，限制为受保护的
   `v*` tag，并按团队策略配置 required reviewers。
3. 确认仓库和 npm 包均为 public，确保 npm provenance 可生成。
4. 不要配置长期 `NPM_TOKEN`。发布 job 仅通过 `id-token: write` 获取短期 OIDC
   身份。

Trusted Publisher 配置与受保护 Environment 完成之前，不得声称公共发布链路已
验收。

## 发布前验证

在 Node.js 24 和 pnpm 11.19.0 下执行：

```bash
cd cli
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test
pnpm build
npm pack --dry-run --ignore-scripts
node ../.github/scripts/cli-verify-package.mjs
node ../.github/scripts/cli-package-e2e.mjs
node ../.github/scripts/cli-verify-release-policy.mjs
```

`cli-verify-package.mjs` 会检查公开包的 README、MIT LICENSE、repository URL、
`mcpcat` bin、tarball 文件白名单，并扫描高置信度凭证格式。实际发布工作流会在
同一次 job 内重新执行 lint、typecheck、test、build、包校验和 tarball 安装冒烟
测试。

## 发布步骤

1. 将 `cli/package.json` 更新为目标版本并提交，锁文件保持一致。
2. beta 使用 `-beta.N`，候选通道使用 `-next.N`，稳定版不带预发布标识。
3. 创建统一的 `v<version>` tag，或基于该 tag 发布 GitHub Release；该 Tag 同时驱动
   npm CLI 与 Docker 镜像发布。
4. `cli-publish.yml` 校验 tag、Release prerelease 状态和 dist-tag 映射，然后在
   GitHub-hosted `ubuntu-latest` runner 上使用 Node.js 24、OIDC 和 provenance
   发布。
5. tag push 和随后发布 Release 可能各触发一次；后触发的 job 会发现不可变版本已
   存在并安全跳过，不会重复发布。

工作流不调用独立的 `npm dist-tag` 命令。dist-tag 只作为单次 `npm publish
--tag` 的一部分移动，因此验证失败或发布失败不会提前移动 `latest`。只有无预发布
标识的稳定版本能够映射到 `latest`。

真实 beta 发布以及全部受支持 Agent 的安装、更新和回滚验证必须在
npm/GitHub 外部配置完成后单独记录，不能用本地 dry-run 代替。
