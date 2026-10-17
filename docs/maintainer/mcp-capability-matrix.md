# MCP 能力覆盖矩阵

## 最终状态（2026-10-18）

第 16.7/16.8 的逐项收尾结果、重跑命令、浏览器闭环及最终部署见[最终验收索引](mcp-final-acceptance.md)。它逐行承接本页原矩阵，没有删除能力或改变范围。下文按时间追加的“尚未完成”“未部署”等是当时的历史事实，不再代表当前待办；本页保留这些记录用于追溯修复和验证过程。

## time-demo 与交互读取收尾（2026-10-18）

新增 `examples/time-skills/server.py` 与 `world-clock.html`：可信源码组合原官方时间工具，附加 `meeting_planner`、`deadline_localizer`、世界时钟 App 和说明资源；服务名、旧参数和三个 Skills 绑定不变。不执行上传的 Skills，不向 App 交付网关凭证。示例配置恢复路径及操作说明见 [Time 演示](../../examples/time-skills/README.md)。

资源、资源模板、提示读取可使用同会话交互运行，显示状态、接收表单/URL/模拟 Sampling、取消及有界清理；结果保留 `contents`/`messages`，不伪装成工具调用。旧同步 API 保留。新增三项真实 SDK 交互读取回归、一项组合协议回归、一项 Widget 提示运行回归；连同原运行测试十二项定向后端回归通过。后端完整 tests 的 1311 项到 100%、退出码 0；前端完整 132 项通过；本轮修改的前端文件静态检查无问题。全量后端运行后仅做 Python 格式化，格式化后上述十二项重验通过。

Web 构建已同步后端和 Docker：源产物、后端静态目录、实际 18081 HTTP 的 main.dart.js SHA-256 均为 `55df54d8c0439e50b3174f4883765cbf9467b5e42418dd05e5a46e728e435bd2`。替换原 `mcpcat` 容器，配置/网络/端口/卷及工具缓存保留，17 张表、6 个 ZIP 在部署前后摘要相同。回退容器 `mcpcat-rollback-20261017-164414`、备份卷 `mcpcat_data_backup_workbench_20261017-164414`，本机清单 `/private/tmp/mcpcat-deploy-20261017-164414`。

部署后通过正式管理 API 只更新 `time-demo` 启动配置，原配置备份于数据卷 `backups/time-demo-before-workbench-1792255523.json`；此为有意的演示配置变更，不混入上段“部署数据未变”的结论。实际发现两个提示、五个资源（包含绑定 Skills）及一个 App，提示读取、App HTML 读取及经 Apps 回调 API 的真实时间查询通过。原 READ 身份重新核验三个工具、三个 Skills 的读取/摘要、时间工具及授权诊断通过；没有修改其他服务。

内置浏览器连接真实 18081，以已有 READ 身份看到两个提示，并填写完整参数取得会议提示消息，显示已完成；世界时钟在隔离 App 中完成初始化、界面正常渲染。嵌套隔离 iframe 的查询按钮被浏览器自动化接口拒绝为目标不可用，未声称浏览器内该按钮点击成功，真实回调以上述 API 验证为证。截图 `/private/tmp/mcpcat-time-demo-prompt-20261018.png`、`/private/tmp/mcpcat-time-demo-app-20261018.png`。未提交代码、推送仓库或发布公共镜像。第 16.7/16.8 的综合逐项验收仍保持未勾选，不能用本次演示替代整张矩阵的剩余证据。

## 收尾增量（2026-10-18）

机器认证前端已接入新增/编辑服务的「配置上游认证」：Basic、POST、私钥 JWT，旧 OAuth 原样保留、未知字段保留、取消不提交、静态 Authorization 冲突拒绝、OAuth 不进入浏览器草稿。服务列表只新增非秘密 auth_mode，卡片不再引导机器来源进入用户登录。新增六项表单/草稿与一项原服务接口保存测试，前端全量 131 项通过；新增组件静态分析无问题。认证可见性、机器工厂/真实网络/JWT、任务输入/取消等八文件 58 项通过。

`test_inspector_task_sampling_gateway.py` 使用公开扩展的合规来源，经生产 create_app/MCPServerManager/InspectorService 和真实 HTTP，三项验证接受/拒绝/取消、工具型 Sampling、同轮 Roots、原 tools/call 一次且不执行返回指令。该证据补齐此前网关缺少合规来源验证的缺口，仍保留固定上游引擎结果校验限制。`test_gateway_legacy_protocol_dates.py` 六项验证真实 2025-03-26/2025-06-18/2025-11-25 客户端到两代来源，逐一核对 initialize 返回版本、工具、资源、提示及未知方法错误，不把 mode 名称当日期证据。

正式构建成功：Apps/上传 24 项、插件 3 项检查通过；源构建、后端静态目录及已部署 18081 HTTP 的 main.dart.js 摘要为 `7d38a457fc41b00256832935a1332924afe508e48f9381191e30b3221abab7d6`。本地镜像 `jeweis/mcpcat:local-workbench-20261018` 已更新原容器，保持端口/环境/网络/数据卷，恢复原工具缓存；健康检查通过，17 张表及 6 个 ZIP 一致，原 READ 身份核验两个工具、三个 Skills、时间调用和授权诊断通过。回退容器 `mcpcat-rollback-20261017-162341`、备份卷 `mcpcat_data_backup_workbench_20261017-162341`，本机清单 `/private/tmp/mcpcat-deploy-20261017-162341`；未公开推送镜像或代码。

内置浏览器在新临时数据库下真实启用 Sampling/Roots、手工回答后工具显示已完成，Roots 调用完成；管理员新增远程服务可打开机器认证表单，字段、说明及取消可用，未将合成配置提交到生产。截图 `/private/tmp/mcpcat-sampling-browser-20261018.png`、`/private/tmp/mcpcat-roots-browser-20261018.png`、`/private/tmp/mcpcat-machine-auth-20261018.png`。第 16.1 的能力范围/版本/公开接口核对已完成；第 16.7/16.8 继续按下表补齐其余交互及验收，不宣称整体协议完全验收。

## 当前交付状态（2026-10-17）

事件与订阅、参数补全、URL 交互、授权诊断四项已完成实现、定向协议/Widget 验证及本次全量回归。真实订阅断流、撤权和进度测试见 `test_inspector_events.py`、`test_inspector_subscription_disconnect.py`、`test_inspector_subscription_revocation.py`；窄屏、旧响应和会话切换见前端对应四个测试文件。既有浏览器补全键盘选择、URL 取消、资源变更及撤权记录仍有效。本次后端完整 tests 到 100%，退出码 0；前端 124 项全部通过。第 16.2/16.6 据此勾选，不代表所有官方扩展完成。

现有构建脚本完成 Apps Host/上传 24 项、Web 插件 3 项检查及 release 构建。前端、后端 static、镜像和实际 18081 HTTP 的 main.dart.js 摘要一致：`783e4077233a0a47b37fea60d62f114afc020519c4f1fbd689b89a7929beb156`。内置浏览器使用新临时数据库验证五入口、新客户端能力对话框及资源读取 MIME；不将这些界面证据扩展为所有交互完整验收。

已部署本地镜像 `jeweis/mcpcat:local-workbench-20261017` 到原 `mcpcat` 容器，保持 18081、原环境/网络及 mcpcat_data 卷。部署前后 17 张数据表及 6 个 ZIP 内容一致，健康检查通过。保留回退容器 `mcpcat-rollback-20261017-074735`、镜像 `jeweis/mcpcat:rollback-workbench-20261017-074735`、卷 `mcpcat_data_backup_workbench_20261017-074735`；本机部署清单在 `/private/tmp/mcpcat-deploy-20261017-074735`。首次 time-demo 连接因冷启动下载依赖超时，合并恢复原 /home/app/.cache 后，原 READ 身份实际验证两个工具、三个 Skills 的读取/完整性、当前时间调用及授权诊断通过；没有自动重放写工具。未推送代码或发布公共镜像。

剩余第 16.1/16.7/16.8 继续收尾机器认证配置入口、任务兼容与版本/传输验收。用户已明确本次不需要企业托管授权，该扩展改为后续独立需求，不作为本次完成条件。其他已确认能力范围保持。

## Sampling 手工交互增量（2026-10-17）

任务取消增量：真实 HTTP 新用例先复现停在 Sampling 问题时取消任务后本地 worker 仍等待回答。现每次任务调用保存独立取消信号，远端确认收到取消请求后中断本地问题回调，再继续查询真实终态；不以取消确认代替 cancelled 状态。回调子任务在会话关闭时同时取消并等待清理。新增回归验证问题清除、旧回答拒绝、来源仍完成时保留实际结果，以及关闭释放等待且不发送迟到回答；与原 HTTP Sampling、任务输入及 Runs 合计 21 项通过，退出码 0。未构建部署，第 16 节完整验收仍未完成。

任务输入增量：真实 HTTP 回归先复现任务 Sampling 未进入交互。固定 fastmcp-tasks 4.1.0 的便捷结果驱动仅处理 elicitation，现由 `inspector_task_inputs.py` 复用公开 `handle.status/result`、`ClientSession.dispatch_input_request` 和 `UpdateTaskRequest` 补齐输入分发，不修改 SDK 私有代码。最多 32 轮、每轮 16 问题、请求/响应各 64 KiB，整轮预校验，沿用测试会话总期限及清理。五个测试文件共 30 项通过（退出码 0），包括真实 HTTP 文本任务 Sampling、普通新旧 HTTP 工具型响应、既有任务表单/取消，以及非法/超限输入不派发。四项图片/音频模型测试验证 MIME/base64 保真，不验证媒体编码有效性。

仍有明确缺口：固定上游 Tasks 的 `input_store.result_type_for_method` 将 Sampling 回答限定为 `CreateMessageResult`，真实 HTTP 的工具型列表响应被上游以 Invalid request parameters 拒绝；客户端分发修复不等于该组合已支持，不删除完整能力目标。后续须验证合规来源及网关转发、错误回答和多问题任务的完整链路，不能修改 SDK 私有类型表掩盖限制。此次未构建 Web、未部署 Docker，也未重新运行全量套件。

正式 HTTP 增量：`test_inspector_sampling_http.py` 通过 InspectorService 正式工厂连接限定新/旧协议的真实本机 HTTP 服务，验证开/关声明、tools/context、手工 tool_use 结果和不会执行返回的 lookup 指令，四项通过。首轮旧协议夹具误将 SDK 消息模型转换为字典，修正为公开 API 所需模型后通过，没有修改生产协议逻辑。另补 UTF-8 请求/响应容量、非法结果、并发问题不替换原 Future、重复回答拒绝和清理测试；三个 Sampling 文件及原 Runs 合计 20 项通过。并发超出当前单问题交互容量时明确拒绝，不覆盖先前问题。媒体内容、真实浏览器和任务型 Sampling 的完整组合仍需验收。

创建会话新增显式 `client_options.sampling`，默认 false，开启时正式工厂装配 SDK sampling_handler 与 tools/context 能力；请求转为当前 InspectorRuns 的有界问题，用户输入 response_json，经 SDK 结果模型验证后返回，不调用模型、不自动执行工具。请求与响应各 64 KiB，拒绝/取消返回错误而非伪造输出。前端增加配置项及多行响应输入，切换身份/服务清除选项。

`test_inspector_sampling.py` 真实内存 SDK 新旧协议 × 接受/拒绝/取消共六项通过，另验证非法 JSON 不完成问题、结束后清理问题、执行记录不含手工内容；连同原 Runs 和客户端配置共 21 项通过。Roots 前端配置与页面级五项回归通过。尚未覆盖完整工具型/多媒体响应、并发问题、正式 HTTP 工厂 Sampling 往返和浏览器交互，未构建或部署；不把模型类可解析等同于完整 Sampling 验收。

## Roots 测试客户端配置增量（2026-10-17）

页面生命周期修复后再次完整运行 Flutter 测试，124 项全部通过，退出码 0；前端 diff 和 OpenSpec 严格检查通过。此时仍未构建 Roots 入口的 Web 或替换 Docker。

页面级增量：`inspector_client_options_integration_test.dart` 直接装配 InspectorScreen 与 API 记录夹具，验证确认配置确实携带 roots 创建会话、切换服务清除配置、等待旧会话关闭时切换服务不会再提交旧配置。新增代际检查位于旧关闭 await 之后、创建请求之前。测试还复现页面卸载时清理调用访问已释放 Riverpod ref 的异常，改用预先取得的 ApiClient；两项通过。初版竞态测试错误地在待完成 Future/加载动画上使用 pumpAndSettle，已改为有限推进动画后切换服务，不将测试超时误报为生产死锁。本证据为 Widget/API 夹具，不代替真实浏览器及身份切换网络验证。

上述入口接入后完整 `flutter test --no-pub` 共 122 项通过，退出码 0；前端 diff 检查及 OpenSpec 严格校验通过。完整套件不替代新增入口的浏览器网络闭环，当前 Web 静态产物仍是上一轮订阅修复版本。

前端增量：新增「客户端能力测试」配置对话框，手动填写每行一个 file URI，默认不声明、可显式空列表；确认后关闭旧会话重新创建，身份/服务变化清除配置，旧配置对话框迟到提交按会话代际丢弃。三个对话框 Widget 用例验证默认/空声明、390 像素布局与非法 URI 拒绝。静态分析新增对话框与测试无错误，既有 inspector_screen 仍有七条 info 提示，未声称全局分析洁净。尚未构建与浏览器验收该入口，Roots 动态变化通知及 Sampling 仍未完成。

创建 Inspector 会话支持可选 `client_options.roots`，复用 SDK 的公开 roots 参数；缺省不声明，空列表声明支持且没有目录。Pydantic 限制最多 32 项、file URI 4096 字符及名称 256 字符，拒绝未知配置，不读取宿主文件、不持久化配置。`test_inspector_client_options.py` 七项覆盖旧请求、空列表、独立模型、非法输入和正式 InspectorService 工厂的真实旧 HTTP 双会话 Roots 响应隔离。连同原工作台、Skills 和 Runs 共 30 项通过。当前仅完成后端入口；前端配置、目录变化通知交互与 Sampling 测试台仍待实现，不将该增量标记为完整客户端能力支持，未部署。

## 订阅撤权状态修复（2026-10-17）

非主动断流增量：`test_inspector_subscription_disconnect.py` 用本机 TCP 转发夹具关闭在途连接，不伪造 SDK 异常。现代 HTTP 验证 worker 结束且源 SubscriptionBus 活跃数归零；旧 HTTP/SSE 首次运行均复现 worker 永久等待。旧 Inspector 订阅现每五秒通过公开 Client.ping 检查，单次五秒超时，随后沿既有 unsubscribe/Client 清理路径退出；现代路径不加 ping、不改原生 listen。三种断流、撤权和原事件测试合计 12 项通过。另补健康旧 HTTP/SSE 等待超过探测周期仍 listening、再撤权清理的回归，三项通过。断网时无法保证源端收到 unsubscribe，不能把本地退出描述成远端已确认取消；旧服务自身仍负责失联会话回收。尚未部署后端或完成本增量浏览器断线验收。

后端清理增量：`test_inspector_subscription_revocation.py` 使用真实本机旧 HTTP/SSE 服务，验证收到资源事件后撤权会结束测试台 worker、清空事件，并向来源实际发送 unsubscribe；恢复权限后等待超过检查周期仍没有自动连接，显式重新订阅后可以正常停止。另一个新用例复现开始订阅前即撤权时残留旧诊断，已在该拒绝分支补清空。三个新增用例连同既有事件及旧代理订阅测试共 12 项通过。使用 SDK Client 与既有旧协议适配，没有新增协议实现；不将这些测试等同于所有非主动断线、传输及跨身份组合完成。本次未部署后端。全仓 diff 检查发现生成的 static/assets/NOTICES 中第三方版权行带尾空格，未改写许可证产物；OpenSpec 严格校验通过。

后续发布构建与浏览器复验：现有 `script/build_web.sh` 完成 Apps 桥接/上传 24 项测试、Flutter release 构建及 3 项插件检查，并同步后端 static。源产物、后端文件、隔离 18766 实际 HTTP 响应的 main.dart.js SHA-256 均为 `57fa10458600752420b0f9ed0bd32a106c9c6aa673e2df0d64f98c96b2f50bfc`。重新登录 READ 测试账号并建立实际 Skills 资源列表订阅后，禁用该临时 Key，浏览器显示“测试会话不可用”，不再显示监听状态或旧确认范围，刷新/开始/停止均禁用。截图 `/private/tmp/mcpcat-subscription-revoked-fixed.png`；测试 Key 随后恢复。该证据验证浏览器鉴权失败处理，不单独证明后端所有监听任务已释放。事件与 Skills 生命周期 Widget 合计 14 项通过；未替换 Docker，非主动断线及完整协议矩阵仍待完成。

隔离服务 18766 的实际浏览器验证复现：READ 测试 Key 禁用后，事件接口失败，但面板仍显示“正在监听”。只修改临时数据库中的测试 Key，验证后已恢复；未操作 Docker 数据。该浏览器证据证明的是修复前问题，不是修复后的验收。

前端现将 401/403/404/410 及服务端 revoked 响应视为当前测试会话不可用，清除旧事件、输入和游标，停止轮询并禁止订阅操作；网络或未确认操作显示状态未知，保留手动刷新/停止，不允许重复开始。没有声称网络失败代表服务端已停止。新增六项 Widget 回归，连同原三项共九项通过；修改的两份 Dart 文件静态分析无问题。首轮测试因夹具计数字段与子类重名未编译，修正测试字段后通过。尚未重新构建 Web、浏览器复验或部署 Docker，第 16.2/16.6 项保持未完成。

核对日期：2026-10-17。固定运行基础为 FastMCP 4.1.0、MCP Python SDK 2.3.0。本文是实施中的验收清单，不是“全部支持”声明。以当前代码、逐项协议测试和浏览器证据为准，不能由框架能力或整体测试数量推断完成。

## 官方范围与判定方法

- [官方规范目录](https://modelcontextprotocol.io/llms.txt)：现代协议 2026-07-28，以及保留的旧协议。
- [官方扩展清单](https://modelcontextprotocol.io/extensions/overview)：Skills、Apps、Tasks、OAuth Client Credentials、Enterprise-Managed Authorization。扩展需分别核对，通用 OAuth 支持不等于两个授权扩展均已实现。
- [弃用登记](https://modelcontextprotocol.io/specification/2026-07-28/deprecated)：弃用不等于立即移除；旧客户端的 Roots、Sampling、Logging 和旧 SSE 仍需兼容。

每项分别检查网关、测试台、正反例和生命周期。表中测试文件是可复验入口，不表示已覆盖该标准的所有条款。浏览器证据及历史运行结果见 [测试台验收记录](inspector-skills-verification.md)。未验收项继续属于提案第 16 节，不能通过删除行或只声明“不支持”来结束完整覆盖目标。

## 服务端资源与交互

| 能力 | 当前实现与证据入口 | 尚需验收或补齐 |
| --- | --- | --- |
| Tools 发现、调用、结构化结果 | `test_inspector_workbench.py`、`test_generic_proxy_metadata.py`；测试台工具入口 | 按标准逐字段核对 annotations、icons、错误和大结果限制 |
| Resources、资源模板 | 同上；列表、按需读取、模板参数、MIME/二进制 | 完整分页、模板边界及浏览器操作矩阵 |
| Prompts | 同上；列表、参数、消息角色和正文 | 所有内容类型与边界逐项核对 |
| Completion | `test_inspector_completion.py`；公开 `Client.complete_mcp`；提示和资源模板参数候选；窄屏提示候选键盘选择已实测 | 资源模板完整浏览器组合继续核对；不能把空候选当不支持 |
| 进度、取消 | `test_inspector_events.py`、输入/任务生命周期测试；SDK progress 回调 | 逐传输核对普通调用、输入恢复、Tasks 三条路径 |
| 现代订阅与旧通知 | `test_proxy_subscriptions.py`、`test_proxy_legacy_subscriptions.py`；公开 SDK `listen`/`ListenHandler`；浏览器接收、停止、新会话清空已实测 | 非主动断线、权限变化的完整浏览器闭环 |
| 平台与聚合变化 | `test_catalog_registry_subscriptions.py`、`test_catalog_upstream_subscriptions.py`、`test_bound_skill_subscriptions.py` | 核对所有来源类型与动态纳入/移出证据；不把事务通知当上游事件 |
| 表单、URL 输入 | `test_inspector_runs.py`、`test_inspector_url_interaction.py`、`test_modern_input_bridge.py`；浏览器 URL 取消返回 action:cancel 已实测 | 最终浏览器矩阵；不自动访问链接或重放业务调用 |
| Logging | `test_proxy_logging.py`、`test_stdio_logging.py`、`test_input_resume_progress.py`、`test_proxy_log_levels.py`；JSON/null 保真、八级过滤、组件输入恢复及阈值生命周期 | Tasks 日志恢复、订阅流隔离仍待补齐；测试台只记录级别，不保留原文 |

## 客户端侧能力与协议基础

| 能力 | 当前实现与证据入口 | 尚需验收或补齐 |
| --- | --- | --- |
| Sampling（包括 tools/context） | `test_gateway_client_capabilities.py`、`test_sampling_modern_roundtrip.py`；公开客户端回调/输入模型 | 测试台可控模拟交互；不接入真实模型冒充协议验证 |
| Roots 与旧变化通知 | `test_roots_notifications.py`、`test_roots_production_lifecycle.py`；公开 session 方法 | 测试台可控目录声明；不能扫描网关磁盘替客户端回答 |
| 现代多轮输入、旧反向请求 | `test_modern_input_bridge.py`、`test_legacy_input_bridge_limits.py` | 完整组合审计；原调用必须只执行一次，恢复状态绑定身份及来源代际 |
| 能力声明 | `test_proxy_capability_transport.py`；公开 transport/session 构造选项 | 所有可选能力逐项对照，不由输入能力通过推断全部声明准确 |
| 协议发现、旧握手及传输 | `test_proxy_cross_generation.py`、升级 Gate A 记录 | 明确每个协议日期/STDIO/HTTP/SSE 组合，不以 auto/legacy 两个名称代替全部版本 |
| metadata、缓存、分页、错误 | `test_proxy_request_metadata.py`、`test_remote_skill_protocol.py` | 按方法核对 TTL/cacheScope、游标、未知字段、标准错误及非法输入 |
| 生命周期、旧 ping、断线 | 来源管理与订阅/输入/任务生命周期测试 | 最终汇总各条路径；现代协议不依赖旧 ping/session id |

## 官方扩展与授权

| 能力 | 当前实现与证据入口 | 尚需验收或补齐 |
| --- | --- | --- |
| Skills | `test_remote_skill_protocol.py`、`test_inspector_skills.py`、`test_skill_directory_read.py`；list/get/read、清单与摘要、目录直接子项和分页 | directoryRead 协议及真实客户端链路已补齐，浏览器实际目录操作仍待验收；不执行上传脚本 |
| MCP Apps | Apps gateway/Host 及 `test_inspector_workbench.py` | 测试台是受限 Host，各 Host capability 单独核对，不声称所有客户端均能渲染 |
| Tasks | `test_proxy_tasks_gateway.py`、`test_stdio_tasks_gateway.py`、`test_proxy_tasks_compatibility.py` | 按官方扩展条款复核完整方法和过期/错误语义，不只验证最终结果 |
| OAuth 发现、注册、授权、刷新 | `test_oauth_discovery.py`、`test_oauth_client_registration.py`、`test_oauth_authorization.py`、`test_oauth_refresh_storage.py` | 上游 OAuth 与网关 Key 分开；不以 OAuth 探测成功推断所有授权路径 |
| OAuth Client Credentials | `test_m2m_provider_contract.py`、`test_oauth_machine_guard.py`、`test_oauth_machine_client.py`；basic/post 已装配上游客户端工厂和后台检测 | 私钥 JWT 产品配置、前端/诊断、完整版本与传输及权限生命周期继续补齐，不以 SDK 探针代替全部验收 |
| Enterprise-Managed Authorization | 用户明确本次不需要，未实现 | 不属于本次交付范围；有企业 IdP 接入需求时另建提案 |

CodeMode 是平台提供的普通 MCP 工具编排入口，不列为官方协议扩展；第三方未知扩展不能仅透传能力名称后宣称支持。服务配置、数据迁移、CLI/ZIP 兼容仍使用原升级验收，不被本矩阵取代。

## 本轮新增证据

`test_proxy_logging.py` 使用真实本地 HTTP 两跳、明确拒绝另一协议时代的来源和两个并发客户端。覆盖现代/旧来源与现代/旧下游、全部八个日志阈值、现代未 opt-in 不接收日志，以及测试台 200 条有界缓冲和关闭后拒收。另验证对象、数组、字符串、数字与嵌套 null 保真，含 msg/extra 的普通对象也不丢其他字段，原文不进入网关应用日志。结合事件及跨时代文件共 47 项通过。

真实探针发现固定 SDK 发送顶层 data:null 时将其省略，接收端因此拒绝通知。后续已通过公开 Pydantic 序列化适配修复网关发送：只将顶层 None 包装为序列化输出 null 的值对象，仍走 SDK 原日志投递与过滤。独立夹具产生合规 data:null，现代/旧 HTTP 及旧 SSE 来源 × 两代下游 × 允许/过滤阈值验证保真与拒收。不将上游缺字段的非法消息补成 null。

`test_stdio_logging.py` 增加两代 STDIO 来源 × 两代下游 × 三个阈值，来源发送全部八级及顶层 null；断言上下游实际协议、原 JSON 和 PID 释放。订阅流隔离、长空闲旧会话阈值及输入恢复时日志路由仍需独立验证。

`test_input_resume_progress.py` 补充两轮输入前后的 JSON 日志，复现现代请求日志缺失并以当前请求回调重绑定修复。两名并发客户端均只收到各自三条日志，原业务仅执行一次；旧客户端同步路径同时回归。后续跨通道竞态需要原调用内最多 200 条/256 KiB 的暂存，`test_input_exchange.py` 验证上限、撤权拒绝和关闭清理；这取代先前空档直接丢弃策略，不持久化日志。资源/提示、Tasks 的日志恢复不由工具测试推断完成。

后续已把上述并发恢复测试扩展到资源模板及提示模板，仍分别断言两代客户端、完整日志和原操作各一次。`test_proxy_log_levels.py` 用缓存返回过期结果的故障注入验证旧设置不失效，并验证容量满不驱逐已有值、同会话更新、失败不占额及实例关闭清理。不是实际等待 24 小时的实机长测；当前阈值表最多 4096 条，保留至端点关闭，不宣称断连立即回收。Tasks 日志及订阅流隔离继续待验收。

运行中旧客户端成功修改 setLevel 的回归先复现仍使用旧阈值，后改为逐条读取该会话当前阈值；测试在首条日志送达后提高级别，再让原调用继续发送，断言后续低级别日志不再交付。它不改变现代逐请求 opt-in。

### 输入响应收尾竞态复验（2026-10-17）

此前单轮通过不代表竞态消失。新增 HTTP 响应帧观测复现：合成上游两名用户共六条日志全部发出，网关响应却缺少其中一条，客户端接收流与网关帧一致，排除了仅客户端回调漏记的假设。进一步通过可控队列稳定证明：问题入队后开始发送的日志尚未完成，ModernInputBridge 就结束输入响应。修复为响应返回前通过同一投递锁解除请求回调，等待在途投递完成，不重放业务，也不增加生产延时。

- `test_input_response_log_race.py`：修复前时序测试失败，修复后通过；额外覆盖请求取消后解除绑定与关闭交换，共 2 项通过。
- `test_input_exchange.py` 与初版时序测试共 10 项通过，验证有界暂存、授权、超时和关闭。
- `test_input_resume_progress.py`：两代客户端 × 工具/资源/提示 × 无延迟/20 ms 测试通道延迟，12 项连续运行 5 轮全部通过。每例均包含两个并发身份、三条日志、两轮输入及原操作仅一次断言；故障信息保留合成上游/网关/客户端三处观测。
- 输入桥接限制、现代输入桥、HTTP/STDIO 日志、日志阈值、客户端能力和现代 Sampling 七个测试文件共 80 项通过。

上述为定向回归，未重跑完整前后端套件，也未重新部署 Docker。Tasks 日志、订阅流隔离以及表中其他缺口仍未完成，不能据此宣布官方能力全部验收通过。

### Skills 目录读取增量（2026-10-17）

依据[官方目录读取规范](https://modelcontextprotocol.io/extensions/skills/overview#reading-directories)，Registry 扩展改为声明 `directoryRead: true`，通过 FastMCP 公开 MethodBinding 注册现代 `resources/directory/read`。根目录及内部目录均从冻结制品清单派生，只返回直接子项；没有服务器文件系统浏览或脚本执行。每页重新验证原版本、权限、完整性和端点，复用 60 秒、128 个快照的有界游标且绑定身份与完整目录 URI。文件内容仍通过原 Resources 接口按需获取，目录结果不能扩充客户端已批准的清单。

`test_skill_directory_read.py` 覆盖 Unicode 文件名、嵌套目录、分页重试、跨身份/目录游标、跨实例/端点 URI、路径穿越、尾斜线、文件型目录、版本冻结、撤权与撤销。真实 FastMCP Client 调用测试台服务层验证现代目录发现/读取及 legacy 普通资源兼容；不把该证据当作浏览器实测。补充真实客户端与来源隔离后目录及测试台定向 14 项通过，最终 `tests/test*skill*.py` 共 169 项通过（14 条既有警告，13.19 秒）。该增量未重建镜像或修改已有数据。

### 事件与交互浏览器增量（2026-10-17）

通过内置 Chromium 浏览器访问 loopback 18766 的 `tests/browser/inspector_fixture.py`，使用临时数据库、固定无生产权限测试身份及后端现有正式 Web 产物；未触碰 18081 Docker。`inspector_workbench_fixture.py` 增加 `notify_demo`，只修改夹具设置及资料版本，实际数据库提交经 RegistrySubscriptions 生成 MCP 通知，再由测试台的真实 MCP Client 接收，不在浏览器伪造事件或 mock REST 响应。

- 原夹具不提供订阅时，界面显示“订阅中断或未获支持”，未伪报监听成功。
- 更新夹具后，订阅 `guide://intro` 得到上游确认范围；关闭面板、执行工具后重新打开，观察到资源列表变化和指定资源更新两条事件。
- 停止订阅后再次执行工具并刷新，仍只有原两条事件、原时间戳；界面为“未监听”。重新连接后面板为空，没有继承旧会话事件。
- 窄屏提示模板 `welcome` 的 `name=Al` 补全返回 Alice/Alex；用空格键选中 Alice，实际提示读取显示“请向 Alice 介绍 MCP 测试台。”，没有调用模型。
- URL 询问点击“取消本次询问”，不打开外部地址；原调用完成且原始响应为 `action: cancel`。
- 授权诊断分别显示 mcpcat 已认证和此端点无独立上游配置，实时探测按钮禁用，不展示框架内部模式或凭证。
- 三个 Flutter 交互测试文件共 7 项通过，包括窄屏和旧会话响应丢弃；事件、补全、URL 后端定向回归共 28 项通过。

本轮真实浏览器为约 418 像素宽的视口。非主动断线、身份撤销、资源模板补全、Skills 目录操作等剩余浏览器矩阵仍未完全验收；不能据此勾选整个第 16 节。截图在当前运行临时目录保存，不作为仓库长期证据附件。

### 机器认证公开接口探针（2026-10-17）

`test_m2m_provider_contract.py` 使用 SDK 2.3.0 的公开认证 Provider 和 httpx2 MockTransport，六项通过：basic/post 获取及复用令牌、错误 issuer 不发送密钥、私钥 JWT 回调只在绑定 issuer 验证后执行，以及初始 scope 挑战行为。所有凭证和 HTTP 响应均为合成数据；不是实际授权服务器或生产网关验收，JWT 回调测试也不是签名密码学验证。

探针确认配置 `scope=read` 时，SDK 仍会按上游 401 挑战请求 `read write`，因此该参数不能直接作为管理员授权范围上限。正式接入须在公开认证流边界限制实际发送的 scope，不修改 SDK 私有方法、不自动扩大配置权限。固定 SDK 元数据模型还要求 authorization_endpoint，机器认证夹具必须提供它才能验证成功路径；该要求不能据此推广为所有机器认证服务器的规范要求。生产配置、生命周期、密钥存储和交互式授权兼容仍未完成。

后续新增 `MachineOAuthGuard`，只包装公开 auth flow，在发送前校验令牌地址和 scope，在 SDK 缓存之前校验授权响应 scope。`test_oauth_machine_guard.py` 五项覆盖正常复用、401 挑战扩权、已授权后 403 step-up、响应扩权及地址变化，结合上述探针共 11 项通过。拒绝路径验证未发送额外令牌请求、不将拒绝的令牌用于资源请求、错误不含密钥。此模块尚未装配进生产客户端工厂；不能据此宣称机器认证已可配置使用，重定向、生命周期及产品链路仍待接入验收。

机器认证重定向增量：FastMCP 的 HTTP transport 默认传入 follow_redirects=True，新增专用公开客户端 factory 强制关闭；测试进一步发现 SDK 仍可能自行处理 307/308，因此 Guard 在令牌响应交回 SDK 前明确拒绝重定向。新增 basic/post × 301/302/303/307/308 × 同源/跨源目标共 20 项，验证目标未收到任何请求、只发生一次原令牌请求且没有携带令牌访问资源。该隔离测试使用 httpx2 实际认证/重定向流程与合成响应，不等于生产 transport 已装配；旧交互式 OAuth 客户端未改变。

客户端工厂接入增量：显式 `oauth.grant_type=client_credentials` 使用新的来源独立 Provider，要求 issuer、token_endpoint、client_id、client_secret 和 scopes；支持 basic/post，拒绝静态 Authorization 混配和已有 credentials_issuer 不匹配。每个客户端只持有自己的内存 token，不恢复旧交互式 token，不把新 token 写回配置。后台检测通过真实 MCP 协商，不再走旧 refresh/browser 路径，迟到结果不能覆盖替换后的来源实例；错误正文不包含上游异常。

`test_oauth_machine_client.py` 通过正式工厂、实际 FastMCP ASGI 服务和合成授权服务完成发现/echo 调用，并验证后台认证状态、独立连接重新获取令牌与配置字节未修改。另覆盖非法配置、旧 OAuth 不进入新分支、检测失败脱敏及实例替换。此证据不覆盖真实网络 TLS、SSE、私钥 JWT 产品入口或完整鉴权生命周期，尚未构建部署。

私钥 JWT 装配增量：机器认证工厂支持 `private_key_jwt`，通过 `private_key_env` 引用服务端环境，限定 RS256/RSA≥2048 或 ES256/P-256，使用公开 SignedJWTParameters 创建 60 秒断言，不在配置保存私钥正文。`test_oauth_machine_jwt.py` 生成临时密钥并以公钥实际验签，验证 issuer/subject/audience、有效期、两个连接 jti 不重复及错误脱敏；共七项通过。与机器认证工厂、Guard 和 SDK 契约共 49 项通过，仍不等于前端、SSE、完整授权扩展声明和真实网络上线验收完成。

机器认证逐请求声明：正式 ProxyClient 工厂在成功装配机器认证时，通过 SDK 公开 `advertise` 添加 `io.modelcontextprotocol/oauth-client-credentials`，不改变其他扩展或调用方的原列表。实际 HTTP 请求体断言覆盖现代发现、列表与调用，旧 OAuth 连接不误声明。测试台授权诊断仍按旧 token 判定，其机器认证状态和操作指引尚需前后端一起补齐，不能把后台机器认证成功当作诊断页已完成。

后续机器认证诊断已接入：快照不获取 token、不把旧 token 视为机器凭证；显式探测通过正式客户端协商并获取短期 token，不调用业务工具、不写配置。真实 ASGI MCP 链路断言探测无 tools 方法；错误不返回异常正文。Flutter 面板显示机器认证说明及显式申请令牌的影响，失败不引导浏览器登录。三个 Widget 测试通过（含 390 像素窄屏），不等于浏览器实测或已构建部署；专用机器凭证配置入口仍待补齐。

交互式入口隔离：`test_oauth_machine_rest_boundary.py` 修复前复现机器认证仍可通过旧 authorize 接口返回 200。现 authorize/set-credentials 在写入前返回 409，机器认证回调不交换令牌，交换期间切换 grant_type 也不保存迟到结果。四个新场景连同原回调、授权上下文及旧 SQLite 刷新共 52 项通过；不改变缺少 grant_type 的历史配置行为。

本轮完整后端命令 `python -m pytest tests -q --disable-warnings --maxfail=3` 已运行至 100%，退出码 0。该进程在交互式入口隔离修复前启动并完成测试收集，因此它是本轮修复前基线的全量证据；新增四项及相关修复以随后 52 项定向回归为准，不声称最终工作树已经再次全量回归。前端完整套件、最终 Web 构建和 Docker 部署仍待执行。

### 机器认证真实流式传输补验（2026-10-17）

扩展工厂测试至现代/旧 HTTP 后，旧机器认证的 tools/list 曾超时。原因是测试 ASGITransport 等待常驻 GET 流完整结束，SDK 的认证流锁因而未释放；不是已确认的生产协议故障。JSON-only 夹具明确返回 GET 405 后，四种协议/认证组合及其余配置校验共 14 项通过。该夹具不用于证明 SSE 正常。

为排除上述夹具调整掩盖真实流问题，新增 `test_oauth_machine_network.py`：通过本机临时 TCP 服务承载实际 FastMCP 和合成授权端点，不替换 HTTP client、不关闭事件流。现代 HTTP、旧 HTTP、旧 SSE 三项均完成发现、工具列表、echo 调用，核验实际协商版本（现代 2026-07-28，旧版 2025-11-25）及每连接仅一次令牌交换；旧连接另核验实际 GET 请求。测试有 20 秒总期限和服务退出清理，不访问生产数据或使用真实凭证。

上述两文件连同 Guard、私钥 JWT、旧 REST 隔离和 SDK 契约共 59 项回归通过。证据只覆盖列明的版本、合成授权服务和本机 HTTP，不等于真实 TLS/企业身份服务、全部旧协议日期或完整权限生命周期验收。未修改生产认证策略，未构建或部署 Docker，第 16 节未完成任务保持未勾选。

### 机器认证探测配置竞态（2026-10-17）

`test_oauth_machine_probe_races.py` 新增换凭证、换地址、替换配置对象及停服 × 成功/失败共八项，修复前均复现迟到探测覆盖当前状态。探测改用独立配置快照创建客户端，写回同时核对实例、配置对象、配置内容及运行状态。测试还验证嵌套 OAuth 字典不是共享引用，不将后续凭证变化混入原探测。没有数据库结构或旧配置迁移。

八项竞态连同工厂、旧 OAuth 管理器及真实 HTTP/SSE 网络测试共 35 项回归通过。该证据限定于后台探测结果，不代表所有运行中业务连接的撤权、凭证轮换或前端配置入口已经验收；未构建部署。

### OAuth 配置读取隔离（2026-10-17）

管理配置 GET 原先允许 READ 身份获取完整 OAuth 字段。`test_oauth_config_visibility.py` 通过真实应用认证中间件及临时数据库，在旧 OAuth/机器认证两种配置下均复现泄露。现在只读响应保留配置入口，将整个 oauth 字段返回 null；WRITE 原响应及存储数据不变，匿名仍返回 401。测试包含 secret、access/refresh token、私钥环境引用和未知嵌套凭证，避免只覆盖已知密钥名称。

上述两项连同旧机器认证 REST 隔离、授权诊断和原授权上下文共 48 项通过。这不是所有配置字段或所有管理端点的完整秘密审计；headers/env/命令参数等既有字段仍需独立核对。专用机器认证表单和剩余协议覆盖仍未完成，未构建部署。

后续只读配置补全采用描述字段白名单，隐藏 headers/env/command/args、所有来源 URL 及未知扩展，WRITE 响应保持完整。`test_oauth_config_visibility.py` 扩展为四种来源配置 × 两种 OAuth 配置共八项，另验证无 OAuth 的旧配置不新增字段及返回值无共享可变引用；结合认证诊断、REST 隔离和业务持久化共 29 项通过。这里四种来源验证的是管理接口的数据可见性，不是四种传输协议调用；其他管理端点仍须独立审计。完整后端回归已启动，结果另行记录，未部署。

### 完整后端及浏览器补验（2026-10-17）

本次 `python -m pytest tests -q --disable-warnings --maxfail=3` 已到 100%，退出码 0；启动在上述只读配置白名单修改之后，包含机器认证网络、配置竞态和读取隔离增量。测试期间未再修改后端代码。此结果仍不证明矩阵中尚未实现的功能已经完成。

复用已运行的 loopback 18766 隔离浏览器夹具、READ 测试身份和现有正式 Web 产物（约 418 像素宽），新增真实界面证据：

- workbench 资源模板 greeting 通过补全按钮取得候选，选择 Alice，再读取得到 greeting://Alice、text/plain 和“你好，Alice”；不将输入框 setValue 的操作意图算作前缀过滤成功证据。
- workbench 没有附带 Skill，显示空列表；从 Skills 管理详情核对远程地址后，连接独立 skills 端点，实际发现 browser-skill 1.1.0 并读取校验后的文件清单。
- 点击读取目录，根目录返回 SKILL.md 及 data/references/scripts 等直接子项；使用实际返回的 references URI 再读，仅返回 guide.md，MIME 为 text/markdown。目录结果独立显示，静态文件查看器仍保留原清单。

截图保存于本轮临时目录 `mcpcat-resource-completion-narrow.png`、`mcpcat-skills-root-directory.png` 和 `mcpcat-skills-reference-directory.png`。目录分页、权限撤销及非主动断线浏览器场景仍未完成；既有 Docker 18081 未变更。本轮浏览器服务没有热重载新认证代码，因此不将这些界面证据用于证明新增只读配置隔离已上线。

### Skills 面板异步隔离增量（2026-10-17）

发现/读取原先仅检查 mounted，未处理同一组件的 session 切换或目录 URI 编辑。现增加请求归属及目录编辑代际，重连清空选择、能力、游标和核验状态；旧能力响应不会继续调用新服务，旧目录结果/错误不显示到新 URI 下。`inspector_skills_lifecycle_test.dart` 五项覆盖延迟发现后换 session、目录请求期间改 URI/换 session 的成功与失败结果；与原 Skills、事件和补全 Widget 测试共 12 项通过。修改文件 dart analyze 无问题。

新生命周期用例初次运行因测试夹具未装配项目 ThemeExtension 失败，补用真实 AppTheme 后通过；没有修改生产主题或忽略框架异常。该增量尚未重新构建 Web，不将前述旧产物浏览器截图用作本修复的浏览器证据。

随后完整 `flutter test --no-pub` 运行结束，113 项全部通过（退出码 0）；本轮修改文件静态分析及前端 diff 检查均通过。未重跑全项目静态分析，不能据此消除之前记录的既有分析警告；最终 Web 构建及部署仍待完成。

### Web 产物同步与重新连接验收（2026-10-17）

执行现有 `script/build_web.sh`，先备份原后端 static 到临时归档，再按锁文件安装 Apps 依赖、运行 24 项桥接/目录上传测试并构建；Flutter clean 后 enforce-lockfile 构建成功，3 项 Web 插件检查通过，实际 release bundle 含文件选择实现。脚本已同步至 mcpcat/static，没有替换 Docker。

frontend/build/web/main.dart.js、mcpcat/static/main.dart.js 及隔离服务 18766 实际 HTTP 返回字节的 SHA-256 均为 `d42b41fd4bba6df55875eb9633cdfccd0b179c436c5315e5de5513a688c17eb1`。内置浏览器重新加载后，用 READ 测试身份完成独立端点 Skill 发现、文件核验和根目录读取；点击重新连接，旧选择、目录结果及直接查询输入清空，新会话重新发现同一已发布 Skill。截图为临时目录 `mcpcat-skills-reconnect-release.png`。

这是当前 Web 产物的正常重新连接路径证据；延迟响应竞态仍以 Widget 回归为证，非主动断线、撤权和完整能力矩阵未完成。旧隔离 Python 进程没有热重载后端新增认证代码，不将前端产物同步描述为生产 Docker 或所有后端增量已部署。
