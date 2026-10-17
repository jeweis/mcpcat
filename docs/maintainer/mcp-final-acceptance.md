# MCP 升级最终验收（2026-10-18）

本页为 `upgrade-fastmcp-v4-and-official-skills` 第 16.7/16.8 项的最终证据索引，逐项承接[原能力矩阵](mcp-capability-matrix.md)，不删减其能力目标。原矩阵后续章节是按时间追加的历史记录，“待补齐”描述以本页所列后续验证为准。

## 逐项结论

测试文件均位于仓库 `tests/`。这里的通过表示本次确认的网关和受限测试台契约通过，不声称对规范所有可能输入组合进行了穷举。

| 原矩阵能力 | 实现与验收证据 | 结果与边界 |
| --- | --- | --- |
| Tools、annotations、icons、结构化结果 | `test_generic_proxy_metadata.py`、`test_workbench_content_matrix.py`、`test_inspector_workbench.py` | 原元数据、未知业务 metadata、六类工具内容及结构化结果保留；不在网关执行返回内容 |
| Resources、模板、MIME、二进制 | 同上及 `test_inspector_read_runs.py` | 原字节、CRLF/Unicode、自定义 MIME、文本/二进制、模板 URI 编码及未知资源错误通过 |
| Prompts 与消息角色 | `test_workbench_content_matrix.py`、`test_inspector_read_runs.py` | 文本、图片、音频、内嵌文本/二进制资源与角色保真；获取提示不调用模型 |
| 四类目录分页与非法游标 | `test_workbench_content_matrix.py` | Tools/Resources/模板/Prompts 经公开分页模型逐页读取，每类至少三页，无重无漏；非法游标拒绝 |
| 参数补全 | `test_inspector_completion.py`、前端补全生命周期测试及原矩阵浏览器记录 | 提示和资源模板均带上下文；窄屏键盘选择及过期响应清理通过 |
| 进度、取消、输入恢复 | `test_input_resume_progress.py`、`test_inspector_events.py`、`test_inspector_task_inputs.py`、`test_stdio_tasks_gateway.py` | 普通操作与任务分别验证；输入恢复不重放原操作，取消不伪称副作用已回滚 |
| 现代订阅、旧 HTTP/SSE 通知 | `test_proxy_subscriptions.py`、`test_proxy_legacy_subscriptions.py`、`test_inspector_subscription_disconnect.py`、`test_inspector_subscription_revocation.py` | 真实断流/撤权/停止及连接释放通过；浏览器断网显示未知，恢复后手动刷新与停止通过 |
| 平台与聚合变化 | `test_registry_subscriptions.py`、`test_apps_registry_subscriptions.py`、`test_bound_skill_subscriptions.py`、`test_openapi_skill_subscriptions.py`、`test_catalog_registry_subscriptions.py`、`test_catalog_upstream_subscriptions.py` | 已提交的可见变化、来源/聚合纳入与移出、回滚不通知及不同身份隔离通过 |
| 表单和 URL 输入 | `test_modern_input_bridge.py`、`test_inspector_url_interaction.py`、`test_inspector_runs.py` | 接受/拒绝/取消、问题 ID、Schema、限额及清理通过；不自动访问外部 URL |
| Logging 与流隔离 | `test_proxy_logging.py`、`test_stdio_logging.py`、`test_input_resume_progress.py`、`test_task_logging_isolation.py` | 八级过滤、JSON/null、输入恢复、任务查询请求日志及订阅流隔离通过；未 opt-in 不发送，不保存原文 |
| Sampling tools/context | `test_gateway_client_capabilities.py`、`test_sampling_modern_roundtrip.py`、`test_inspector_sampling_http.py`、`test_inspector_task_sampling_gateway.py` | 新旧来源/客户端、工具型结果、多问题 Tasks、手工回答与拒绝/取消通过；不调用真实模型 |
| Roots 和旧变化通知 | `test_roots_notifications.py`、`test_roots_production_lifecycle.py`、`test_inspector_client_options.py` | 声明、并发身份、HTTP/SSE/STDIO、数据库撤权与进程释放通过；不扫描服务器磁盘 |
| 跨时代多轮输入 | `test_modern_input_bridge.py`、`test_legacy_input_bridge_limits.py`、`test_input_response_log_race.py` | 原操作单次执行、绑定身份/参数/服务代际、两轮回答、取消/撤权/停服、收尾竞态通过 |
| 精确能力声明 | `test_proxy_capability_transport.py`、`test_m2m_provider_contract.py`、`test_inspector_client_options.py` | 回调能力按实际下游收窄，机器授权仅向对应上游声明；不开启客户端模拟能力时不冒充支持 |
| 协议日期与传输 | `test_gateway_legacy_protocol_dates.py`、`test_proxy_cross_generation.py`、STDIO/SSE 日志与任务文件 | 现代 2026-07-28；三个旧 HTTP 日期逐一断言 initialize 返回值；HTTP/SSE/STDIO 两代链路分别验证 |
| metadata、缓存、错误 | `test_proxy_request_metadata.py`、`test_remote_skill_protocol.py`、`test_generic_proxy_metadata.py`、`test_workbench_content_matrix.py` | 逐跳身份头隔离、业务 metadata、TTL/cacheScope、完整清单游标、未知 URI/方法和非法输入通过 |
| 生命周期、旧 ping、断线 | 输入、Roots、订阅、Tasks 生命周期文件及 `test_time_demo.py` | 旧连接有界探测、现代不依赖 ping；停止/撤权/重启释放连接，演示生命周期复用同一 PID，退出后 PID 确实消失 |
| 官方 Skills 和目录读取 | `test_remote_skill_protocol.py`、`test_skills_protocol_security.py`、`test_skill_directory_read.py`、`test_inspector_skills.py` | list/get/read、固定版本/清单/摘要、目录及权限通过；浏览器 30 文件目录两页读取到 guide-29，末页无下一页 |
| MCP Apps | `test_upstream_apps_proxy.py`、Apps Host/安全/装配测试及真实 time-demo 浏览器 | Prefab/HTML/附件/同名来源、受限 Host 桥接通过；真实按钮→确认→工具→页面时间完整闭环通过 |
| Tasks | `test_proxy_tasks.py`、`test_proxy_tasks_gateway.py`、`test_stdio_tasks_gateway.py`、`test_proxy_tasks_compatibility.py`、`test_task_logging_isolation.py` | get/update/cancel、终态/输入/错误、身份隔离、过期拒绝、旧同步路径及原进程持有通过；使用轮询，不虚构可选任务推送 |
| 旧 OAuth | `test_oauth_discovery.py`、`test_oauth_client_registration.py`、`test_oauth_authorization.py`、`test_oauth_refresh_storage.py` | issuer/PKCE/state、DCR、刷新、回调及已有凭证流程保持 |
| OAuth Client Credentials | `test_oauth_machine_client.py`、`test_oauth_machine_network.py`、`test_oauth_machine_jwt.py`、Guard/可见性与前端表单测试 | Basic/POST/私钥 JWT、HTTP/SSE、短期令牌、范围/地址绑定、重定向拒绝、旧配置/未知字段与取消保存通过 |
| Enterprise-Managed Authorization | 用户此前明确不需要 | 不在本提案范围，不因完成本提案声称实现 |

CodeMode 按[独立验收](code-mode-verification.md)保持普通 MCP 工具编排定位，不冒充官方扩展；旧 ZIP、CLI、数据及恢复证据见[升级证据索引](upgrade-evidence-index.md)。

## 最终运行与部署

- 后端完整 `pytest tests -q --disable-warnings --maxfail=3`：1315 项，100%，退出码 0。本轮新增的分页/多媒体/任务日志与演示进程测试另行定向复验通过。随后真实部署发现逐操作启动上游影响演示列表耗时，调整为演示生命周期显式持有；该调整后两项演示回归及最终包含生产 Tasks 生命周期的 16 项定向回归通过，容器内候选完整发现/提示/UI/时间查询 3.67 秒通过，不将修改前全量结果描述为修改后重跑。
- 前端完整 Flutter 测试：133 项全部通过。新增独立测试台请求预算回归：Web 适配器在首个响应进度前仍计算连接超时，实际 App 列表 10.62 秒会触发原 10 秒误报；现在测试台使用 45 秒预算容纳后端 25/30 秒期限，普通请求仍为原预算，不自动重试。修改文件静态检查无问题，正式构建的源产物、后端 static 和最终 Docker 实际 HTTP 的 main.dart.js 摘要一致，均为 `93a36a1401914aa9238a8f1ce056c998180726463eb639ec7691fcf173820714`。
- 现有 CLI：76 项通过，TypeScript 类型检查通过；Gate C 文档/版本一致性脚本通过。这不意味着自动发布公共 npm 或完成另一提案的全部外部发布演练。
- 最终镜像仍为本机 `jeweis/mcpcat:local-workbench-20261018`，替换现有 `mcpcat` 容器，健康检查通过。原端口、环境、网络、数据卷与工具缓存保留，17 张表及 6 个 ZIP 摘要一致。
- 回退容器 `mcpcat-rollback-20261017-171525`、数据卷备份 `mcpcat_data_backup_workbench_20261017-171525`；本机清单 `/private/tmp/mcpcat-deploy-20261017-171525`。回退必须遵守完整快照流程，不仅降级依赖。
- 正式 time-demo 保留原工具与三个 Skills；Prompt、App 已加入同一端点。固定旧版 Time 上游单独使用 legacy 协商，演示生命周期明确持有连接、退出统一关闭，关闭隐式 keep-alive；不改变通用网关自动协商和逐身份隔离策略。
- 最终容器使用原 READ 身份重新核验：3 个工具、2 个 Prompt（均经交互读取接口成功）、1 个 App 及其真实时间回调、3 个 Skills 的读取与完整性全部通过。该轮观测创建会话 2.26 秒、工具列表 4.92 秒、提示列表 5.22 秒、App 操作约 8.68 秒、时间回调 9.15 秒；是本机观察值，不作为性能 SLA。

## 浏览器证据

- 真正 App 查询：`/private/tmp/mcpcat-final-app-query-complete.png` 为最终 Web/容器，`/private/tmp/mcpcat-time-demo-app-query-complete.png` 为前一轮证据。Chrome 完成内嵌按钮点击、宿主确认、真实查询及显示结果；最终重载后列表无误超时，查询显示 `2026-10-18T01:19:02+08:00`。这补齐此前内置浏览器接口不能点击嵌套 iframe 的验收缺口，没有关闭沙箱。
- 网络断开：`/private/tmp/mcpcat-subscription-network-unknown.png`。仅切断隔离测试 TCP 代理，显示未知并禁重复开始；恢复后显式刷新、停止成功。该场景不宣称浏览器离线会立刻取消远端订阅，来源断流释放另由真实协议测试证明。
- Skills 目录第二页：`/private/tmp/mcpcat-skill-directory-page-two.png`。仅向隔离夹具上传合成的 30 文件包，观察 guide-25…29，末页下一页消失。原固定清单与实时目录结果分开。
- 其他 Sampling/Roots、机器认证、URL、补全、撤权、文件选择与取消、Skills 重连证据承接原矩阵中的记录和对应 Widget/真实协议回归。

本轮自建的断线代理和浏览器测试页已关闭，原隔离服务未停止；合成分页包只存在该服务临时目录中，未写入正式数据。正式环境保留备份以供恢复。

## 已知边界，不是未关闭待办

平台仍是单实例管理中心与网关，不是通用 Agent Host。不会自动执行上传 Skills、请求模型、授予磁盘权限或保证所有第三方 Agent 自动激活/渲染。受限 App Host 只声明实际具备的能力；未审核外部域名/不具备的 Host 权限明确拒绝。多媒体测试验证协议编码和原始字节，不宣称浏览器解码器支持所有媒体格式。固定 Tasks 来源执行引擎对工具型 Sampling 回答的限制仍存在，合规来源经本平台的对应转发链路已独立通过；不会修改上游私有类型表或伪造成功。

以上边界均承接已确认设计；本次功能与综合验收已闭环，不以移除能力行、忽略失败或缩小旧客户端范围完成任务。没有自动归档、提交/推送或发布公共镜像。
