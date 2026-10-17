# FastMCP 4.1 升级兼容证据索引

当前增量的逐项覆盖与未完成项见 [MCP 能力覆盖矩阵](mcp-capability-matrix.md)。下文 Gate 记录保留历史时间与范围，不能替代第 16 节的最终验收。

2026-10-15，固定版本的实现/验收记录，不是发布公告。真实浏览器官方 SDK 参考 Host 与管理预览交互已通过；第三方商业 Host 未验证，不把参考 Host 结果扩大为所有 Agent 支持。最终任务审计见提案清单。

| 范围 | 固定环境/结果 | 证据与边界 |
| --- | --- | --- |
| Gate 0 依赖/API | Python 3.11.14，FastMCP 4.1.0，MCP SDK 2.3.0，Prefab 0.20.2，Monty 1.1.0 | [实施记录](fastmcp-v4-upgrade.md)、`uv.lock`；使用固定包公开 API，不把文档 main 当实现版本 |
| Python 最低版本 | 真实 Python 3.10.19 定向 263 passed/26 warnings，无跳过；原最低版本保持 | [跨版本修复及证据](python-runtime-compatibility.md)；AnyIO 公共期限/TaskInfo 与异常组 backport，不依赖 3.11 独有接口 |
| Gate A 协议/上游 | 现代 2026-07-28 与 legacy；STDIO/SSE/HTTP/OpenAPI、跨时代代理、Inspector/Catalog、取消/热切换 | [Gate A 完整记录](fastmcp-v4-upgrade.md#gate-a四类上游的真实网络矩阵)、仓库 `scripts/probe_*`；旧 HTTP 版本另有固定原型记录，不把 SDK legacy 一个模式等同于所有历史客户端 |
| OAuth | 现代发现、legacy 回退、401/传输错误、issuer/PKCE/state、DCR 与 SDK CIMD 路径、旧 token 刷新/手动自动回调 | [OAuth 用户指南](../user/upstream-oauth.md)与实施记录，`test_oauth*` / `probe_oauth_network.py`；不扩大既有 Catalog 的 OAuth 纳入规则 |
| Gate B 数据 | 原 v1.0.1 DDL → head 0005；九旧表逐列、未知字段/凭证、原 ZIP/bundle、重复/中断/损坏恢复 | [数据 Gate B](fastmcp-v4-upgrade.md#gate-b101-旧数据等价失败关闭与重试)；合成旧 Schema 夹具，不伪称生产数据已升级 |
| Gate C 官方 Skills | ext-skills 167da6c、conformance c37eec8 / 0.2.0-alpha.12；39 success、6 可选跳过 | [协议及实际客户端矩阵](skills-protocol-verification.md)；Inspector 2.11.0 公共包 list/get 摘要通过，2.6.0 modern 解析失败；不宣称 Agent 自动激活，不声明 directoryRead |
| 原 MCP/CLI | mcporter 0.13.7；公共 npm CLI 1.0.1、Node 24.20.0；10 Agent × 2 Scope、200 命令 | [公共 CLI 记录](published-cli-upgrade-verification.md)；默认 local，remote-only 不干扰 pin/update/rollback；不是远程 Skills Host |
| Gate Code | FastMCP CodeMode / Monty，modern/legacy，逐工具授权、双来源凭证、配额/网络取消/部分写入 | [CodeMode 记录](code-mode-verification.md)；原 Catalog/Skill 绑定不替换，新增开关备份/恢复与模板不变两项通过 |
| Apps 网关/管理 | 官方 Host bridge 2.0.3、Prefab 0.20.2；HTML 装配 1–5；来源/代际/隐藏回调、12 项执行中撤权矩阵 | [Apps 安全矩阵](apps-security-matrix.md)、[来源绑定](catalog-upstream-apps-routing.md)、[装配](html-module-assembly.md)；SDK/协议/浏览器中继夹具不冒充真实第三方 Host |
| 旧程序完整恢复 | 原 v1.0.1 commit，FastMCP 3.4.4 / SDK 1.25.0；原 HTTP/SSE + 公共 CLI 10 命令 | [完整恢复演练](upgrade-recovery-drill.md)；升级后数据库/新 Skill/App/CodeMode 文件与设置另存且 SHA 不变，不静默丢新写入 |
| 版本/发布门禁 | 应用 1.0.1、CLI 1.0.1、generator 1.1.0、Registry v1 / 1.0.0、独立迁移/协议/renderer/Python | [版本策略](upgrade-version-policy.md)和机器矩阵；14 项门禁测试通过，不发布或覆盖同名版本 |
| Gate D | 后端 785 passed/70 warnings，Flutter 90 passed，定向 analyze 无问题，CLI 76 passed/typecheck/lint/build/pack 安装通过 | [双模式 UI 与实际构建](skills-ui-design.md)、[参考 Host 与管理预览交互](apps-host-verification.md)；固定 SDK HTML/Prefab、同名来源路由及关闭取消通过，不宣称第三方产品认证 |

Skills、UI 扩展、CodeMode 各有不同边界：UI 能力由 FastMCP 自动声明；Skills 需要逐请求 opt-in 且服务启用；CodeMode 是普通工具编排，不是官方扩展方法；未知上游扩展不自动透传。普通 MIME/binary/metadata 代理保留，不把所有资源限定为 Markdown。

上述自动化验收仅使用临时配置/数据库/制品及合成凭证，不重启用户实例、不执行上传 Skill、不写系统 Keychain。后续用户授权的本地 Docker 实例升级单独记录如下。具体旧版/第三方客户端的“未验证”不是“不支持”；需要记录确切版本和真实操作，不能从协议或官方功能清单推断激活/渲染成功。

本提案 Gate C 指官方 Skills 分发协议，与前置 `build-skills-management-platform` 的 CLI 对外发布演练 Gate C/16.6 不同；本次不把前置提案未授权或未完成的外部发布任务勾选。完整 Docker 镜像构建及本地既有实例切换已执行；远程生产环境切换、Windows/Linux 实机及第三方商业 Host 产品认证仍未执行。

## 用户授权的本地 Docker 升级（2026-10-15）

- 使用现有 Compose、端口、凭证和持久化数据卷替换本地容器，未发布远程镜像或修改产品版本。先保留旧镜像，停止旧容器后完整复制数据卷；逐文件 SHA 一致，备份 SQLite 完整性检查为 `ok`。只读备份不创建 WAL 辅助文件，使用 `immutable=1` 检查停机后的静态快照。
- Dockerfile 改为从 `uv.lock` 导出生产依赖并校验哈希安装，避免构建重新解析出未验收的传递依赖。镜像实测 FastMCP 4.1.0 / MCP SDK 2.3.0 / Prefab 0.20.2；空数据隔离容器启动、健康检查和迁移通过。
- 修正 `.dockerignore`：排除本机 `.mcpcat/` 数据与 CLI `node_modules`，但保留运行时 Markdown Skill 模板。镜像内数据目录为空，四种 local/remote × service/catalog 模板、Apps 前端资源和最新迁移均存在。
- 既有数据库从 `0002_skills_registry` 自动迁移至 `0005_app_render_review`，完整性为 `ok`。九张旧业务表逐列比较完全一致，原 Skill ZIP 字节不变；两个原有 MCP 服务保持运行，管理 API 和 Web 页面返回 200。
- 两个既有 STDIO 来源通过真实 HTTP 客户端的 auto/legacy `tools/list`，分别返回 9 和 4 个工具，未执行业务工具。Redis 来源首次验证发现新容器 npm 缓存中的依赖文件截断，按该缓存的锁文件执行 `npm ci --ignore-scripts` 后两种模式均通过，未修改来源配置或数据卷。`main.dart.js` 与 `apps-preview.js` 的 HTTP 返回字节与镜像内构建产物一致。
- 首次部署当时保留旧开关语义，Apps 管理关闭时 `/api/apps` 返回 404。此历史行为已被下述“固定提供平台能力”修订和再次部署取代，不代表当前行为。
- 回退必须同时使用保留的旧镜像和匹配的升级前数据快照；不得直接让旧程序读取迁移后的数据库。回退前还须另存升级后的新增数据。本次未执行真实实例回退，也未执行用户上传的 Skill 脚本。

## 最终任务审计

### 统一测试台与文件选择补验（2026-10-17）

- 工具、资源/模板、提示模板、Skills、应用及会话任务/表单交互完成；普通产品入口不展示框架模式名称，原始协议内容保留。最终后端完整回归 829 项、Flutter 98 项、Apps bridge 23 项通过，动态入口增量协议 5 项通过，Web 构建与插件检查通过。
- 内置浏览器完成 Skills ZIP 与 Apps HTML 选择回显，Skills 取消状态已观察，Apps 原生取消由用户执行并确认；无自动上传或生产写入。第 12.2 项完成，完整记录见 [测试台与文件选择验收](inspector-skills-verification.md)。
- 统一测试台已同步到后端静态目录，但尚未覆盖现有 Docker；下文 Docker 记录均为此前明确授权的部署，不代表此次增量已上线。无 Git 提交、推送、归档或对外版本发布。

### Skills 统一文件夹与 ZIP 入口

- 单一拖放区识别目录或 ZIP；点击展开目录/ZIP 两种原生选择器。目录在浏览器内打包，保留相对路径，显示进度、清单和排除项，必须确认后才上传。原 ZIP 字节及服务端校验、版本、绑定和审核契约保持不变，没有执行 Skill 内容。
- 新增目录遍历/打包 10 项测试，Apps Host Node 总计 22 项通过；Flutter 总计 91 项通过，修改的 Dart 文件静态分析通过。发布脚本检查上传模块产物存在，原插件构建检查继续保留以覆盖 Apps。
- 独立无头 Chrome 验证真实目录选择器读取 107 个文件、二进制保留、ZIP 原样读取、取消事件清理、ZIP DataTransfer 拖放、目录条目递归事件、非法输入/锁定/销毁。生成的 ZIP 经真实后端校验得到 107 个文件。目录拖放使用合成 FileSystemEntry，取消使用浏览器 cancel 事件，不宣称完成所有操作系统拖拽/系统取消实机验收。
- 临时空数据 Docker 实例运行完整 Flutter 页面：实际目录选择、未确认不得提交、确认后窄屏 multipart 上传、服务器草稿成功及 `.env` 排除均通过。仅写入合成测试资源，未向既有实例上传。测试使用独立浏览器，不修改用户 Chrome 扩展权限；CanvasKit 使用发布包同版资源消除 CDN 对测试启动的干扰，未宣称完整离线字体或所有浏览器认证。
- 真实页面测试发现 Flutter 默认 DialogRoute 的 opaque 语义层遮挡嵌入的原生 DOM 控件；上传入口改用保留主题和模态遮罩的 GeneralDialog，显式取消关闭。重跑真实点击和完整上传通过，不通过强制点击或移除全局语义层掩盖问题。
- 可重复脚本为前端 `tool/test_skill_upload_browser.mjs` 与 `tool/test_skill_upload_e2e.mjs`；后者要求显式指定一次性空实例及合成凭证，不可用于生产资源库。Apps 原文件选择器没有改为此模块，其历史系统选择框人工验收仍单独跟踪。
- 2026-10-15 已将 `jeweis/mcpcat:local-folder-upload-20261015` 部署到既有容器，本地 `latest` 指向新镜像，沿用 Compose、18081 端口、凭证及数据卷。保留镜像 `jeweis/mcpcat:rollback-before-folder-20261015`、停机完整数据快照 `mcpcat_data_backup_folder_20261015`，并保留来源依赖缓存。上线 SQLite 完整性 `ok`，17 张表逐行一致，原 ZIP 字节一致；两个既有 MCP 均 running，健康与鉴权 API 正常，实际 HTTP 返回 `main.dart.js`/`skill-upload.js` 与镜像构建产物逐字节一致。未提交 Git 或发布远程镜像。

### Web 文件选择插件构建回归

- Skills 上传点击选择文件时报 `pickFile() has not been implemented`，实际发布 JS 未包含 Web 实现；构建缓存中复用了加入 `file_picker_web` 前的注册文件。插件的锁定 Web 版本支持该方法，并非后端上传校验或鉴权失败。Apps 上传使用同一插件，同步修复。
- 工作空间 `script/build_web.sh` 在正式构建前执行 `flutter clean`、锁文件约束下的 `pub get`，再构建并检查实际 `main.dart.js` 的插件 DOM 标记，通过后才同步后端。`frontend/tool/verify_web_plugins.mjs` 的 3 项测试通过；故障旧产物被检查拒绝，新产物通过。升级插件需复核标记，静态检查不替代浏览器验收。
- 本轮 Flutter 90 项回归通过，重新构建本地镜像 `jeweis/mcpcat:local-picker-fix-20261015` 并替换既有容器，健康检查通过。保留 `jeweis/mcpcat:rollback-before-picker-20261015` 和 `mcpcat_data_backup_picker_20261015`；上线后 17 张表逐行比较及原 ZIP 字节不变，两个既有 MCP 来源均 running，实际 HTTP 返回的 JS 与新镜像一致。
- 真实 Chrome 新标签页已分别触发 Skills 与 Apps 单文件选择事件，不再抛出未实现异常；自动填入无害测试 ZIP 被浏览器扩展的本地文件权限阻止，未点击上传、未写入资源库，临时标签页已关闭。文件读取回显、系统选择框取消和完整上传仍需补充人工验收，不能用 Widget 注入式测试冒充已验证。

### 固定提供平台能力（取代此前平台开关设计）

- Skills 管理/分发、Apps 管理/分发和 CodeMode 固定提供，移除环境变量及配置模型中的平台总开关；旧 enabled/independent_enabled 输入兼容忽略，读取时不回写历史配置。原服务和 Catalog 的生命周期、资源发布/撤回、认证及安全限制仍保持。
- 启动先保留旧 MCP/Catalog 地址，再优先保留已显式配置的系统地址，冲突的新增系统入口持久化避让；不覆盖旧服务、不因新默认名称阻止旧实例启动。手动提交冲突路径仍返回 409，保存后待重启的 Skills 路径也被保留。
- 前端取消 CodeMode 启用按钮，保留限额、工具范围、状态及真实地址；Apps 的错误提示不再要求启用平台功能。沿用既有主题与 Riverpod，保留窄屏、错误重试及重复提交保护。
- 完整后端 805 passed / 70 现有及依赖 warnings，Python 3.10 定向 58 passed；Flutter 90 passed，修改的 5 个 Dart 文件定向分析无问题，CLI 76 passed。新增默认/旧 false/旧环境变量、路径优先级/幂等避让、待重启占用、新旧客户端计算与沙箱拒绝宿主读取回归。原资源撤回、Key 撤销、来源停止及执行中范围收紧仍有回归，不以删除旧开关测试替代安全验收。
- 已通过现成 `script/build_web.sh` 重建 Web 和固定 Apps Host bridge 并同步后端静态目录；依赖/协议/数据库版本门禁与 OpenSpec strict 通过。本段测试使用隔离数据，不代表第三方 Agent 产品认证或对外发布。
- 已重新部署现有 `mcpcat` 容器，沿用 Compose、18081 端口、凭证和 `mcpcat_data`。新镜像为 `jeweis/mcpcat:local-fixed-capabilities-20261015`，本地 `latest` 指向该镜像；未推送远程镜像。隔离容器带旧的两个 false 环境变量仍正常提供能力，auto/legacy CodeMode 计算通过。
- 替换前保留镜像 `jeweis/mcpcat:rollback-before-fixed-20261015125529` 和停机数据快照 `mcpcat_data_backup_20261015125529`，备份逐文件 SHA 一致。上线后 SQLite 完整性为 `ok`，全部原业务表记录及旧 ZIP 字节不变，原 runtime identity 记录保留，Schema 仍为 `0005_app_render_review`。
- 上线实测 Skills/Apps/CodeMode 均 ready，配置不再返回平台开关；三类匿名 MCP 请求返回 401，管理接口返回 200。两个原 MCP 来源均 running，auto/legacy 分别列出 9/4 个工具，CodeMode 两种模式均列出 3 个工具；未执行业务工具或上传 Skill。PostgreSQL 来源首次启动重新下载依赖后通过，Redis 沿用替换前缓存。Web 两个主要 JS 的 HTTP 内容与镜像内文件逐字节一致，容器健康检查通过。

### Review 后制品生命周期修复

- 删除从未发布的 Skill 时，在写事务中清除变体引用，只回收已无引用的 remote 制品记录；事务提交后清理对应文件。仍被其他变体引用的共享制品和已发布历史保持不变，提交失败不删除文件。
- `diagnose_artifacts` 同时检查 local 与独立 remote 制品的大小、SHA 和文件状态，正常 remote ZIP 不再误报孤立文件；恢复原字节后可重新诊断为 `ok`。诊断不会自动删除孤立文件，也不会自动清理本修复前已产生的残留。
- 新增 `tests/test_skill_remote_artifact_lifecycle.py` 的 9 项回归，覆盖 local/remote/共享/不同字节、共享引用保留、提交失败、文件缺失/同长度损坏及恢复；Python 3.10.19 与 3.11.14 的相关 61 项回归通过。仅使用临时数据，未运行用户数据库清理。
- 修复后 Python 3.11 完整后端回归 794 项通过；首次沙箱内执行有 14 项在本机临时端口绑定阶段被环境拒绝，授权后完整重跑全部通过。修改文件 Black/isort/Flake8 定向检查及 OpenSpec strict 校验通过；本轮无前端修改，不重新构建 Web。

### 升级原始验收记录

- Gate 0/A/B/C/Code/D 按本次提案定义通过；逐项版本、数据、协议、来源/权限和客户端边界由上表对应记录支撑。最终官方 conformance/Inspector 和完整旧程序恢复在 Python 兼容修复后再次执行成功。
- 最终后端 785/785、Flutter 90/90、CLI 76/76；Host Node 12/12、来源中继 Node 11/11、版本漂移 Node 14/14。保留后端 70 个现有/依赖警告，未用隐藏失败或跳过必需协议场景换取通过。
- 本轮修改的 Python 文件 Black/isort/Flake8、前端定向分析、OpenSpec strict 与新增文档相对文件链接检查通过。源码 diff 空白检查通过；生成的 `static/assets/NOTICES` 保留上游版权文本的尾空白，不手改该构建产物或声称所有未改历史文件均已全量 lint。
- 浏览器参考 Host、管理页和下载只操作临时目录/端口；已关闭本轮创建的标签、服务和 iframe。未升级用户当前虚拟环境、启动/重启用户 MCP、迁移用户真实数据库、推送 Git 或发布 npm/Docker。
- 设计规范影响本轮界面：沿用现有壳层与主题的明确兼容例外，补齐双模式状态、持续反馈、键盘焦点和准确的无障碍模式标题；不将协议升级冒充全站主题/读屏认证。
- 后续正式发布须选择新的产品版本并按发布流程审批，不能覆盖既有 v1.0.1；提案可由维护者另行归档，本次不自动归档或发布。
