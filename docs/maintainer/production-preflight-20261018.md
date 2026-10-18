# npm / Docker 发布前验收（2026-10-18）

> 本页保留修复前的历史结果。后续依赖安全阻塞已修复，最终镜像审计、完整回归与浏览器真实查询结果见 [生产依赖安全修复与补验](security-dependency-remediation.md)。尚未推送或发布。

## 结论

**功能回归通过，但当前候选镜像未通过依赖安全门槛，暂不批准生产发布。** 本轮未提交、推送、创建 Tag、发布 npm/Docker 或归档提案。原 mcpcat 容器和数据卷未替换。

这不是“零问题生产认证”：未进行公网压测、独立渗透测试或所有第三方 Agent/浏览器组合验收。

## 功能与制品

| 检查 | 本轮结果 |
| --- | --- |
| 后端完整回归（Python 3.11 验收环境） | 1323 passed，384 warnings，307.47 秒；无失败/跳过 |
| Docker 锁定依赖上的协议与 Apps 专项（Linux arm64 / Python 3.12） | 66 passed，19 warnings；临时测试工具使用 `--no-deps`，未替换生产依赖 |
| Flutter 全部 Widget/逻辑回归 | 148 passed |
| CLI 类型、Lint、构建、命令兼容 | 129 passed，类型/Lint/构建通过 |
| npm tarball 安装 | 版本命令及三组新管理入口帮助通过；只在临时目录安装，无公共发布 |
| Apps Host / 上传桥接 | 24 passed |
| Web 文件选择插件校验 | 3 passed，正式产物含实际 Web 实现 |
| 升级兼容漂移检查 | 14 passed；发布版本一致性、CLI Gate C 文档检查通过 |
| 正式 Web 构建与同步 | 通过，使用工作空间既有 `script/build_web.sh` 同步到后端 static |
| Docker 构建 / 启动冒烟 | 通过；UID 1000、healthy、匿名读取受保护管理接口被拒绝、FastMCP 4.1.0 / MCP SDK 2.3.0 |
| OpenSpec strict 与 diff 空白检查 | 通过 |

正式构建的 `main.dart.js` 在 frontend/build/web、后端 static、候选容器磁盘与实际 HTTP 返回一致，SHA256 为 `e9a869ce06d9b2f941157f05d32f6c407d2c05ec3c73bf7721201d141270bcc9`。未将该候选部署到原容器。

### MCP 不是只测管理 REST

本轮完整套件包含真实 FastMCP Client、公开 MCP SDK 模型、回环 HTTP/SSE 与 stdio 子进程测试。Docker 锁定环境额外执行：

- `test_time_demo.py`：同连接 Tools、Prompts、Resources、App 声明/读取、工具调用和 stdio 进程释放。
- `test_remote_skill_protocol.py`、`test_skills_mcp_gateway.py`、`test_skills_source_gateway.py`：远程 Skills 发现/读取、原字节与摘要、权限、撤回、绑定和来源隔离。
- `test_apps_mcp_gateway.py`、`test_app_management_api.py`：App 声明、资源/资产读取、上传发布撤回、真实工具回调、撤权和匿名边界。
- `test_proxy_legacy_subscriptions.py`：真实回环连接上的旧 HTTP/SSE 通知与订阅清理。
- `test_workbench_content_matrix.py`：分页、内容类型、结构化结果及错误边界。

CLI 的 `test_cli_management_http.py` 已包含在完整套件中：构建后的用户 CLI 经随机端口连接临时 SQLite/制品目录的真实 mcpcat API，验证上传、审核发布、读取、下载、撤回、READ 拒绝写入及旧 `skills list` 兼容。它不是新增给最终用户的测试命令。

### 浏览器补验边界

现有 READ 登录在 Apps 中心发现 time-demo，跳转测试台并加载世界时钟隔离预览成功。当前内置浏览器自动控制无法点击两层隔离 iframe 内的查询按钮，报告目标不可交互；**本轮不宣称已重新完成“App 内点击→宿主批准→真实时间结果”的浏览器验收**。已关闭预览，真实工具开关恢复关闭。

上述回调与授权链路的代码/协议回归通过；此前完整浏览器证据见 [最终验收](mcp-final-acceptance.md)，不能用旧截图冒充本轮新点击。双架构工作流虽配置 amd64/arm64，本轮镜像实际运行只覆盖 arm64。

## 依赖安全：阻塞发布

- CLI 的锁定生产依赖与 Apps Host 的 npm audit 均报告 0 个已知漏洞。
- Python 验收环境：114 个包，pip-audit 报告无已知漏洞，`uv pip check` 通过。
- **实际 Docker 镜像**：108 个包中有 9 个包被标记，共 62 条公告记录。记录存在重复，同包/公告 ID 去重为 40 项，不等同 62 个独立可利用漏洞。尚未完成公告有效性、可达性及修复版本兼容性的逐项评估，不能据此声称所有问题都可远程利用或都为高危。

| 镜像中的包 | 镜像版本 | 公告记录数 | 验收环境版本 |
| --- | --- | --- | --- |
| PyJWT | 2.10.1 | 21 | 2.15.1 |
| Pygments | 2.19.2 | 2 | 2.21.0 |
| AnyIO | 4.12.1 | 2 | 4.15.1 |
| cryptography | 46.0.3 | 13 | 50.0.2 |
| pip | 25.0.1 | 12 | 未安装 |
| pydantic-settings | 2.12.0 | 1 | 2.16.0 |
| python-dotenv | 1.2.1 | 2 | 1.2.4 |
| requests | 2.32.5 | 2 | 2.34.2 |
| urllib3 | 2.6.3 | 7 | 2.8.0 |

差异源于验收环境依赖比镜像生产锁文件更新；不能用开发环境“无公告”代替生产制品审计。后续需审查并更新锁文件/基础构建工具，维持已批准 FastMCP 与协议兼容基线，再重建、重跑协议/CLI/Apps 与制品审计；无法修复的条目需明确书面风险处置。未经授权本轮未批量升级依赖或屏蔽公告。

原始本地日志：`/private/tmp/mcpcat-production-backend.log`、`mcpcat-production-locked-protocol.log`、`mcpcat-production-frontend.log`、`mcpcat-production-analyze.log`、`mcpcat-production-web-build.log`、`mcpcat-production-docker-build.log`；审计 JSON 为同目录的 `mcpcat-production-{cli,apps,python,image}-audit.json`。临时目录可能被系统清理，本页保留汇总与边界。

## 其他发布前事项

- `flutter analyze` 没有错误，但有 4 条既有警告与 62 条提示，命令退出非零；不记成零告警通过。警告为无效 default、未使用 catch stack、import 和 field；本轮不夹带全库风格重写。
- Docker 构建有既有 `$PNPM_HOME` 同一 ENV 声明引用警告；应在发布清理时处理。
- 当前统一产品版本仍是已使用的 `1.0.1`，不得复用同名 npm 不可变版本 / Git Tag。新管理 CLI 应随新的统一版本发布，而不是把源码候选当成 npm 已有能力。
- 目前工作流：推送 main/master 构建并推送 Docker（默认分支可更新 latest）；版本 Tag / Release 触发 npm 发布。没有 PyPI 自动发布工作流。版本 Tag/Release 可能同时触发，现有 npm 不可变版本检查用于避免重复发布。
- CLI CI 不代替后端完整测试；现有 Docker 工作流也不包含本轮全部后端、前端与真实客户端门禁。可复验不代表这些门禁已全部自动纳入 CI。
- 提交时区分 frontend、mcpcat 子模块及工作空间指针，排除无关 mcpcat-market 指针和 `.pnpm-store/`。本轮不自动提交、推送或归档。
