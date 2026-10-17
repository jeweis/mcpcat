# FastMCP 4.1 升级实施记录

本提案实现与范围内验收已完成，尚未提交、推送或对外发布。2026-10-15 已按用户授权构建 Docker 镜像并升级本地既有实例，保留原配置和数据，详见 [兼容证据总索引](upgrade-evidence-index.md)。以下逐阶段记录保留其历史进度，不能把早期“仍待完成”或单个原型结果当作当前状态。

原始完整后端 785 passed/70 warnings，Review 修复后为 794 passed；Flutter 90 passed、CLI 76 passed，官方 Skills 39 个成功检查（可选目录读取 6 项未声明而跳过）及 Inspector 2.11.0 复验通过。[官方 SDK 参考 Host / 实际管理预览](apps-host-verification.md) 已覆盖上传及同名多来源 HTML/Prefab 的真实渲染、确认、动态回调与关闭取消；[完整旧程序恢复](upgrade-recovery-drill.md) 与 [Python 3.10 最低版本 263 项矩阵](python-runtime-compatibility.md) 通过。版本漂移门禁现为 14 项。未知第三方 Host、远程生产切换与其他平台实机不作未验证承诺。

最新补充：固定 Provider/扩展生命周期 API 审计及已托管 Apps 的 Catalog 接入见 [专项记录](catalog-apps-integration.md)。1.6 完成；9.8 上游同名资源/回调聚合现已完成，后续真实 Host 和完整安全矩阵仍单独验收。

CodeMode 后续完整验收见 [Gate Code 记录](code-mode-verification.md)：任务 5.14/5.18 已完成，现代/legacy 普通客户端通过真实代理工厂验证独立上游凭证、撤权、同名来源、跨 Key 配额、网络取消和调用限额。修复 JSON-only 路径取消不传播，使用框架原生流式响应。CodeMode 63 项纳入后端完整回归，当前结果 660 passed、27 warnings；下文早期阶段记录的“未挂载/待验证”描述只代表当时进度。

任务 6.4 的 [公共 npm CLI 1.0.1 验收](published-cli-upgrade-verification.md) 已完成：真实旧发行包连接临时升级实例，10 Agent × 两种 Scope、200 次命令全部通过，包括 local 更新/pin/rollback、remote-only 不干扰旧安装和 doctor。未改全局 CLI、系统 Keychain 或用户 Agent 安装目录。

9.6 已完成：新草稿使用 html-assembly-5，在固定语法解析器和客户端包内清单基础上补齐计算型/嵌套 import、作者 imports/scopes/null/前缀、多 map 合并、模块 query/fragment、JSON 包装和计算型附件。不透明 sandbox iframe 实测父 DOM 不可访问、表达式只求值一次、普通及 query 作用域正确，原始 ZIP/已冻结 1/2/3/4 保持不变。清单展开在序列化前限制大小/数量，固定 JS 资源随临时 wheel 包含。后端 698 项通过、70 个警告，详见 [HTML/Prefab 装配验收](html-module-assembly.md)。原 Prefab 0.20.2 renderer、动态 state/已绑定动作回归保持通过；9.8/9.12/9.13 和整个提案仍未完成，早期默认 assembly-3/4 描述只代表当时阶段。

9.8 已完成：正式 Catalog Provider 以来源 UUID/运行代际区分同名上游 App，支持打开、UI/附件资源和隐藏回调，CodeMode 排除 App/UI-only 工具。当前 Key/来源/Schema 在查找和调用时重检，Prefab 原始数据不改写，重复框架 hash 不进入全局路由。现代/legacy 原生 Client 与实际工厂代理认证回归通过，普通/opaque Host 浏览器夹具的隔离与取消通过。完整后端 741 项、定向 28 项及 Node 11 项通过，仍有 70 个既有/依赖警告，详见 [来源绑定与中继验收](catalog-upstream-apps-routing.md)。真实独立 Host、完整矩阵和恢复/构建仍属于未完成的 9.12/9.13 与 Gate D。

9.12 已完成：补齐真实 TCP 工具/资源/上传 App 的取消和来源停止清理，修复现代 JSON 响应断连不取消的问题；旧协议仍使用 SDK 原生取消。修复 App 过滤后普通歧义别名复活的问题，普通/App/CodeMode 共用过滤前冲突判断。12 项执行中撤权矩阵确认结果拒绝且不重试/伪称回滚。完整后端 768 项通过、70 个警告，来源中继 Node 11 项通过。逐项证据与真实 Host 边界见 [Apps 安全矩阵](apps-security-matrix.md)；这不完成 9.13 或整体 Gate D。

8.3/8.4 后续验收已完成：[完整恢复演练](upgrade-recovery-drill.md) 使用原 Git v1.0.1、FastMCP 3.4.4/SDK 1.25.0 恢复匹配旧数据库与制品，HTTP/SSE 和公共 CLI 10 次生命周期命令通过；独立升级后快照保留新增 Skill/App/CodeMode 数据。新增 [独立版本门禁](upgrade-version-policy.md) 及 12 项漂移测试通过，不提升产品/CLI 版本、不覆盖历史制品。下文早期“完整恢复未完成”的描述仅代表当时阶段，整体提案仍有未完成任务。

## Gate C：固定官方场景与真实 Skills 客户端

完成 1.5/5.10，详细固定版本、原始场景、客户端配置/限制和复现步骤见 [Skills 协议验收与客户端矩阵](skills-protocol-verification.md)。规范固定 ext-skills 167da6c，官方一致性工具固定 conformance c37eec8；不修改官方断言，仅经公开连接选项提供每请求 Skills opt-in。真实应用工厂/独立端点/SQLite/制品与 TCP 验证中，enumeration 32 项、manifest 6 项、directory 的 wire schema 1 项成功，无失败或警告；未声明 directoryRead 的 6 个可选检查跳过，不算已实现。

使用公共 npm Inspector 2.11.0，临时 HOME/client config/OAuth 路径及 memory secret store，分页 list --verify 校验三个 Skill、十二个文件，再逐个直接 get --verify，全通过。每个包包含 600000 字节二进制、CRLF 中文参考与不执行的脚本。旧 Inspector 2.6.0 的默认 legacy/opt-in/现代结果解析限制按实际失败记入矩阵，不对服务器标准响应作降级改写。检查器验证不代表第三方 Agent 自动激活或执行客户脚本。

官方测试发现裸 fastmcp 资源 metadata 不符合命名建议；仅对自有 Skill FunctionResource 的公开 get_meta 做命名空间覆盖，原 tags/version 保留，通用/上游 metadata 不改。新增正式 MCP 恶意 URI get/read、发现后摘要/frontmatter 漂移拒绝、另一线程真正提交发布前后的 HTTP 快照读取；结合既有身份游标/Key 撤销、公开审核、关闭/路径/来源作用域、512 文件/16 MiB 边界与大于 UI 预览限制的读取回归，完成本阶段服务器分发安全 Gate C。

本轮新增六项本地回归，全量后端 642 项通过（27 个警告），格式/导入/diff/OpenSpec strict 通过。未修改运行数据库、安装用户 Agent Skill、执行上传脚本、部署或发布。其他客户端激活、完整 Apps Host、CLI/恢复/部署 Gate 仍待验收。

## Gate B：1.0.1 旧数据等价、失败关闭与重试

完成 3.6/3.7。`tests/legacy_v101_fixture.py` 固定本地 Git `v1.0.1` 原始 0001/0002 DDL 的 SHA-256；两份历史迁移与 Tag 完全一致，夹具只升级到 `0002_skills_registry`，不是先建新表再伪装成旧数据库。使用合成配置/凭证、上传与生成来源、草稿/发布/退役/归档四种状态、稀疏固定 ID、未知嵌套字段、历史审计、四个 ZIP 和一个缓存 bundle。原有九张业务表全部有记录，快照逐列保留原 JSON 字面值、日期与凭证；这是原 Schema 数据夹具，不是读取生产库或运行旧版服务。

`test_v101_data_upgrade.py` 验证 0002 → 0005 后九表逐列相等、原配置完全相等、原 ZIP/bundle 原字节及 SHA 不变；四种状态各新增一个 local 引用，原 artifact ID/状态不变，remote 全部缺失。通过正式应用工厂再次自动升级，并以原自定义认证头请求 registry、明确历史版本下载与历史 bundle 路由，索引形状和返回原字节不变。已有缓存 bundle ID/SHA/manifest 被复用，重复升级不重打包。

`test_v101_upgrade_recovery.py` 在 local 回填之后、runtime identity 写入时注入失败：0003/0004 新表及 revision 完整回滚到 0002，业务 manager 未加载；一致性 SQLite 备份保留原数据/revision，可复制到独立路径读回原九表，修复后重试成功。连续三次重复升级不新增/改变 variants、runtime identities 或备份；旧备份未覆盖。另以 Skill/bundle × 缺失/同大小字节损坏四组验证正式下载返回 409，不返回损坏内容或改用 remote；恢复精确原文件并诊断后下载和索引恢复，原旧表再次逐列相等。

新增专项 8 项通过；全量后端 636 项通过（27 个警告）。本节只判定数据增量升级和本地快照/制品恢复 Gate B，不把临时库复制当成完整生产回退演练：停写、保存升级后新数据、匹配旧程序启动以及公共 CLI pin/rollback 的完整恢复仍由 8.3/6.4 验收。没有升级运行数据库、执行 Skill 脚本或覆盖用户文件。

## Gate A：四类上游的真实网络矩阵

完成 2.10：`scripts/probe_gateway_matrix.py` 用真正的 `MCPServerFactory`/manager/Catalog/Inspector 路由，在回环随机端口和全新临时 SQLite 执行：

| 入口 | STDIO 子进程 | HTTP MCP | SSE MCP | OpenAPI HTTP |
| --- | --- | --- | --- | --- |
| HTTP auto / HTTP legacy / SSE legacy | 全通过 | 全通过 | 全通过 | 全通过 |
| 已发布 mcporter 0.13.7（离线缓存安装） | 通过 | 通过 | 通过 | 通过 |
| Catalog MCP search → call（auto / legacy） | 全通过 | 全通过 | 全通过 | 全通过 |
| Inspector REST 创建会话 → Schema → 调用 → 关闭 | 通过 | 通过 | 通过 | 通过 |
| Catalog REST 搜索 → 调用 | 通过 | 通过 | 通过 | 通过 |

共 32 个按来源分解的成功场景（报告将 Catalog MCP 的四来源合并为一行，输出 26 行）。实际协商为 auto 2026-07-28、legacy/SSE 2025-11-25。OpenAPI 的原始字符串经官方 Provider 包装为 structuredContent.result：直接已知 Schema 客户端取得字符串，Catalog synthetic call 无原 Schema 时保留 result 对象，REST 也不擅自扁平化。测试显式核对这一契约，不丢弃 metadata 或假装所有结果同形。STDIO 仅启动仓库可信 echo 夹具；OpenAPI 同步文档预加载在工作线程，避免与同事件循环的测试上游互相阻塞。

矩阵修复了 Inspector 的 URL 传输推断：服务名恰为 sse 时，已知 `/mcp/sse` HTTP 入口会被 SDK 自动猜为 SSE。现在使用公开 StreamableHttpTransport 明确传输，原 REST 形状/应用级 session 不变；增加专门回归，并由真实 Inspector REST × sse 来源验证。

跨协议/认证证据合并此前 `test_proxy_cross_generation.py` 四组现代/legacy 下游 × 单时代 HTTP 上游、`probe_proxy_network.py` 六组 HTTP/SSE 两跳及 mcporter、`probe_oauth_network.py` 的真实 TCP HTTP/SSE 探测，以及本次全量回归中的 DCR/CIMD/旧预注册 × auto/manual × auto/legacy 十二条真实序列化授权/刷新链路。OAuth 完整链使用内存 ASGI 的实际协议服务，不能描述成第三方真实授权账户；通用网络矩阵不包含登录型生产来源。Gate A 仅判定本提案固定运行时、上述传输/路由/授权契约通过，不代替 Skills/App Host 激活、数据迁移、恢复或发布 Gate。

本轮后端全量 628 项通过（27 个警告），OpenSpec strict、相关格式/导入和 diff 检查通过。探针的进程、端口、Inspector 会话和临时数据已清理，未访问运行实例或用户配置，未发布。

## 来源生命周期与 ASGI 热切换

完成 2.4：初始与动态来源统一交给 `ServiceLifecycles`，每个公开 FastMCP lifespan 在同一个任务内进入和退出；不操作 SDK 私有 session/task group。启动等待真实就绪，不再靠固定 sleep；同来源增删启停重启串行化，其他来源不共用管理锁。授权探测和 Catalog 刷新由 registry 跟踪，停止来源和应用退出时等待取消清理。超时或退出失败保留失败状态与未清理记录，不谎报停止，也不启动第二个代际。

`MCPProxyApp` 每次请求选定一个目标代际，逐条转发 ASGI 消息，不缓冲流。响应头发出后的错误不再二次发送 500，错误正文不泄露上游异常。管理端停止会取消请求并等待业务 finally：未发送响应头时返回 503；已开始的 HTTP 流发送终止帧；WebSocket 关闭。客户端自己的取消仍传播，不伪装成成功响应。连接终止不意味着副作用回滚，调用方不得据此自动重试写入。重启先验证、准备候选；无效候选不停止旧服务；保存新配置失败恢复旧生命周期。删除持久化失败保留可重试记录；删除后同名重建复用所属代理路由，不累积重复挂载。

新增生命周期/ASGI 18 项测试：初始/动态启动、并发启动去重、启动失败及超时恢复、慢退出阻止二次启动、后台清理、无效重启、删除失败重试、活动真实 MCP 调用取消、流式消息即时转发、代际快照、异常边界和外部取消。现代和 legacy 使用真实 MCP HTTP 序列化/内存 ASGI；不是只断言状态字段。`scripts/probe_manager_lifecycle.py` 另在临时 SQLite 和真实 TCP 上验证四个生命周期阶段 × auto HTTP/legacy HTTP/legacy SSE，共 12 项；保持 SSE 连接时停止、随后重启和删除后重建均通过，四次进入/退出为相同所属任务且退出后无来源/请求/后台任务。工厂边界替换为本机原生 FastMCP，未运行用户 STDIO 命令，因此此项不替代四类真实上游 Gate A。

本轮全量后端 627 项通过（27 个警告）；共享网络辅助函数变更后，两跳 HTTP/SSE × auto/legacy/mcporter 0.13.7 六项复验通过。未访问用户数据库、重启现有服务、同步部署静态资源或发布制品。

## DCR/CIMD 选择、issuer 绑定与旧刷新链路

完成 2.9：新增 `oauth_client_registration.py`，通过固定 SDK 的 OAuthClientMetadata/OAuthClientInformationFull、create_client_registration_request、handle_registration_response/check_registration_usable、should_use_client_metadata_url、create_client_info_from_metadata_url 和 credentials_match_issuer 实现已有身份 → 显式且来源支持的 CIMD → DCR/手工的选择。旧 client_id 优先，不在升级时自动重注册。SDK 响应解析会删除 wire issuer，mcpcat 仅用已发现/配置的 issuer 绑定注册结果。DCR 单次请求、不跟随重定向、256 KiB/总计 15 秒；校验实际 token 认证方式和回调替换，保存 basic/post/none，拒绝无法使用的注册后保留原 needs_credentials 引导。

CIMD 只使用明确文档 URL，不按“client_id 看起来像 URL”误判；无 secret，复用 SDK none 身份。CIMD 凭证可移植性不延伸到 token：token_issuer、token_client_id 仍限定既有令牌。缺失绑定的旧记录按 SDK 兼容，不为它臆造历史 issuer。原手工 setter 明确换身份时不继承其他 client 的 secret/token，成功授权后才记录新 token 的绑定；来源重建、注册期间配置变化或保存失败不发布新的授权状态。

`oauth_refresh.py` 统一后台旧 token 检查/刷新，读取当前配置；前后验证来源对象、地址、headers、运行代际、OAuth 快照和停止状态。先持久化再切换内存，失败保留旧数据并报告过期。核对本地 Git `v1.0.1:app/models/mcp_config.py` 的原字段形状，新增真实临时 SQLite/Repository 刷新用例：原 client_id/secret、顶层和 OAuth 未知字段保留、refresh token 未旋转时保留、不凭空添加 issuer。没有访问用户数据库。

新增 DCR/CIMD/issuer 23 项、完整链路 12 项、注册/刷新竞态 11 项、实际 SQLite 1 项及 Uvicorn 回调查询脱敏 4 项；补充旧 helper 非法 expiry 与回调前后停止拒绝。完整链路是 DCR/CIMD/旧预注册 × auto/manual 回调 × auto/legacy Client，通过原 REST、真正的 MCP HTTP 序列化与内存 ASGI 上游验证 PKCE、令牌认证方式、实际 Bearer 调用、后台刷新后下一调用生效；不是只比较配置对象，也没有创建真实外部 OAuth 账户。CIMD 文档由授权服务器验证，测试不冒充第三方 AS/Host 实际认可。本节取代下方历史阶段的 2.9 待办描述；完整四类上游 Gate A、数据迁移/恢复和实际发布验收仍分别待办。

参考 [FastMCP CIMD](https://gofastmcp.com/clients/auth/cimd) 与 [MCP Authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)，实现接口以固定 SDK 源码为准。业务日志不打印注册错误正文；应用工厂为标准 uvicorn.access 安装幂等查询过滤器，覆盖自动回调的路径前缀/编码场景。反向代理、外部日志平台和用户自定义日志仍须单独脱敏，不能声称该过滤器控制所有日志。

本轮全量后端 609 项通过（27 个警告），相关格式/导入/diff/OpenSpec strict 校验通过。未运行用户数据库升级、重启服务、覆盖部署静态资源或发布制品。

## OAuth 单次授权与实时 token 取用（历史阶段：2.9 实施中）

新增 `oauth_authorization.py`，复用公开 PKCEParameters.generate 和 validate_authorization_response_iss。授权状态使用高熵随机值、恒时比较与单调时钟五分钟期限，保存深拷贝配置、来源对象及地址/传输/headers/运行代际快照；同服务新授权替换旧尝试。旧 state 不含服务名，返回字段与原自动/手动回调地址保持不变。保留授权端点已有查询（如 prompt），替换冲突 OAuth 参数，并为明确资源发送 resource indicator。声明不支持 S256 时不降为 plain。

回调拒绝重复/空 code/state/iss、error、过期/重放/其他来源、配置变化及错误手动 redirect URI；有绑定时使用 SDK 严格核验响应 issuer，声明需要 iss 而缺失拒绝。历史无 issuer 绑定且无 iss 的原流程保留；未知 iss 不盲目放行。token 请求前检查原来源/配置，完成后再次检查，防止请求期间同名重建、换 URL/凭证后将旧 token 写入新来源。SQLite 持久化失败不先修改内存或报告成功。新增诊断不记录 state、授权码、回调 URL、响应正文或 token。

新增 `oauth_tokens.py` 统一授权码/refresh 请求和标准响应校验，保留旧 secret-post 默认，支持明确 basic/post/none；refresh 响应不旋转 refresh token 时保留旧值，expires_in=0 按已过期处理。拒绝 token 重定向，错误只记录状态/异常类型。新成功回调记录 token_issuer/credentials_issuer，明确绑定不匹配时不发送，历史缺失绑定仍兼容。此处不是完整跨 issuer 注册/迁移策略，DCR/CIMD 选择与绑定仍需继续完成。

修复代理捕获创建时 token 的问题：每次新 ProxyClient 从同一来源当前配置读取有效 token；授权完成、刷新、移除与过期实时生效，保留入站头不转发策略。actual MCPServerFactory + 真实 HTTP MCP 序列化/ASGI 回归，在 auto/legacy 两模式中依次观察 first/refreshed/撤销/过期，不靠 transport 属性断言冒充实际传输。

新增 62 项（state/PKCE/issuer/来源 29、真实 REST 回调 16、token/刷新/真实代理 17），与既有 OAuth 发现合计 104 项；旧默认无绑定/无新字段及回调返回结构继续验证。全量后端 555 项通过（16 个警告）。2.9 尚未完成完整 DCR/CIMD、注册 issuer 绑定及旧凭证刷新到真实上游的全矩阵，因此不提前勾选；未修改运行数据库、重启用户服务或发布。

## OAuth 现代探测与授权元数据发现

完成 2.8：`oauth_probe.py` 直接使用固定 FastMCP Client(mode="auto") 与公开 HTTP/SSE transport，不自行生成 discover/initialize 或实现另一套时代协商。只实际 401 进入授权发现；超时、连接、HTTP、版本不兼容与协议错误分别生成固定安全诊断，后台 manager 标记失败，原 authorize REST 返回 502，不再返回 NONE 冒充就绪。探测不调用业务工具、不启动 OAuth 浏览器；取消继续传播。

`oauth_metadata.py` 复用 SDK 2.3 的 WWW-Authenticate 提取、PRM/授权服务器/OIDC URL 构建、标准模型、resource 匹配和 validate_metadata_issuer。顺序为 challenge URL、路径/根 PRM、其第一个授权 issuer 的 OAuth/OIDC 元数据；无 PRM 时兼容旧根目录发现。有效 PRM/issuer 不匹配及服务端/连接错误明确阻断，不跨 issuer 回退旧根目录。元数据请求不携带配置的上游 headers/Key，不跟随重定向，拒绝非 HTTPS（回环开发除外）、userinfo、片段、控制字符及非法端口，单份 256 KiB/请求 10 秒/总发现 30 秒。

manager 同次发现保留 challenge 并直接持久化所得元数据，避免第二次丢失自定义 PRM 地址。保留原手工 client_id/secret、scope、回调模式及未知字段；存量 token 分支不在此次探测重授权或删除。迟到探测不覆盖同名重建来源。原 needs_credentials 引导继续保留，issuer/CIMD/PKCE 能力标记保存在新发现配置供后续绑定验证，但这不是 CIMD 注册、授权响应 issuer、token 刷新或完整自动/手动回调验收，2.9 保持待办。

`tests/test_oauth_discovery.py` 与 `test_oauth_probe_manager.py` 专项 42 项通过，包含实际 Client/ASGI 现代与仅旧协议、HTTP/SSE 401、自定义 PRM/独立 issuer/OIDC/legacy root、凭证不跨元数据请求、401 无元数据、网络/超时/HTTP/不相交版本错误、issuer/resource/容量/非法 URL/重定向、取消、manager 失败状态/迟到结果、原凭证和 REST 引导。协议探测额外验证同 origin 的 307 尾斜杠重定向，以兼容常见 MCP 路由；跨 origin、HTTPS 降级或含 userinfo 的跳转在发送下一请求前拒绝，配置的自定义认证头不跟随转出。元数据发现仍一律不跟随重定向。`scripts/probe_oauth_network.py` 在临时 cwd 与真实 TCP 回环分别取得 HTTP 2026-07-28、SSE 2025-11-25，无用户配置/数据库或浏览器交互；测试服务已退出。参考 [FastMCP OAuth 文档](https://gofastmcp.com/clients/auth/oauth) 与 [MCP 授权规范](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)，具体 helper 以已安装固定 SDK 源码为准。

本轮全量后端 493 项通过（13 个警告），相关格式/导入/diff/OpenSpec strict 校验通过。没有运行数据迁移、覆盖后端静态资源、重启用户服务或发布制品；提案整体仍有后续 Gate 待验收。

## 两跳独立协商与真实网络/会话过期验证

完成 1.4/2.6，并修复跨时代代理缺口：原实现按下游 legacy 模式选择上游，旧客户端访问只支持现代协议的来源会失败。现在 HTTP/STDIO 上游统一使用公开 SDK 的 auto 协商/回退，只有 SSE 传输固定 legacy；没有自写握手 dispatcher。`tests/test_proxy_cross_generation.py` 四组真实 MCP HTTP 序列化/ASGI 回归覆盖现代/旧下游 × 仅现代/仅旧上游，核对实际工具及资源结果、本跳版本以及 discover/initialize 的探测行为。原 metadata 回归按各跳独立时代核对框架标准 serverInfo，未知业务 metadata 不丢弃。

`scripts/probe_proxy_network.py` 使用真实临时 TCP 两跳、实际 MCPServerFactory 和离线缓存 mcporter 0.13.7，六组验证通过：auto/legacy/mcporter 分别经 HTTP 网关访问 HTTP/SSE 来源。旧客户端到网关实际为 2025-11-25，HTTP 上游仍为 2026-07-28；现代客户端到网关为 2026-07-28，SSE 上游仍为 2025-11-25。配置 timeout=17 的客户端独立实例断言仍通过；兼容协商不把 timeout、401 或其他连接错误任意解释成“旧协议”。

旧 HTTP 保留 `MCPCAT_MCP_HTTP_SESSION_IDLE_TIMEOUT=0` 默认，即公开框架设置 None，不因新 SDK 默认期限意外过期；管理员可设置有限正秒数。`scripts/probe_session_expiry.py` 真实 TCP 分别验证 None 和 0.1 秒：无持续 GET 的旧会话空闲 0.4 秒后，前者仍调用成功，后者明确返回 404；失败动作没有执行，也不自动重放。显式去除失效 session 并重新 initialize 后取得不同 session，后续新动作成功。持续 GET 可能保持旧会话活跃，不能仅等待就断言已过期。现代 auto 客户端同样等待后正常调用，不依赖 ping 或 session id 做业务健康判断。

恢复策略是客户端明确重新连接/握手，用户判断后再发起业务调用；网关不在未知执行结果时自动重试整段请求。上述脚本只操作回环夹具，不读取用户配置/数据库或重启现有服务。专项代理 28 项与全量后端 451 项通过（12 个既有警告）。这不是 OAuth、四类上游完整矩阵、第三方 Apps Host 或完整发布 Gate 验收，2.4/2.8/2.9/2.10 等仍待完成。此节取代下文早期实验中“旧请求选 legacy 上游”的策略描述。

## 通用代理 metadata 与请求头边界

完成 2.11：新增 `tests/test_generic_proxy_metadata.py`，对 auto/legacy 两种实际协商模式、两个同名组件来源，完整比较 prompts/list/get、resources/templates/list/read、tools/list/call 的模型字段。覆盖 prompt/模板/tool icons、resource annotations、自定义 MIME、CRLF/中文/emoji 原文与嵌套未知 metadata。标准 serverInfo 仍单独按固定框架语义核对，不全局剥离未知字段；结合此前二进制 blob 与 HTML/Prefab 资源回归，普通网关不退化为 Markdown/Skills 专用通道。现代 Client 实际发现 `io.modelcontextprotocol/ui: {}`，保留框架声明不等于第三方 Host 会渲染。

完成 2.5：转发策略复用固定 ProxyClient(forward_incoming_headers=False)，入站用户头的默认白名单为空；只发送管理员明确配置的上游静态 headers/Auth 以及 SDK 为本跳生成的协议头。新增 `tests/test_proxy_request_metadata.py`，客户端→网关→上游均使用真实 HTTP MCP 序列化、公开 transport/client factory 与内存 ASGI 承载。测试实际记录入站 Key/私有头确实到达网关，再由三种上游方法回显证据：tools/call、resources/read、prompts/get 的业务 `_meta` 均保留，配置的 X-Configured 存在，Mcpcat-Key/X-Inbound-Private 不存在；下游专属 clientInfo 名称不跨连接复用。现代 Mcp-Method 与上游执行方法一致，协议版本头匹配本跳上下文；legacy 仍正常使用自身协议头。连接协商专属 metadata 的过滤/重建由官方实现负责，没有第二套私有 dispatcher 或协商引擎。

本轮新增 4 项回归，相关普通/Apps 代理 8 项通过，全量后端 447 项通过（12 个既有警告），格式/导入/diff/OpenSpec strict 通过。这里只证明上述内存 ASGI 两跳及同代现代/legacy 场景，不冒充真实 TCP、四类上游全矩阵、跨时代组合、生命周期或 OAuth 刷新验收；2.4/2.8/2.9/2.10 等继续待办。没有改动运行数据、部署静态文件或重启用户服务。

## HTML 原生经典脚本调度

9.6 继续实施：新草稿使用 `html-assembly-3`，包内经典 script 的原始 UTF-8 字节经 SRI 验证后编码为 data:text/javascript;charset=utf-8;base64 URL，生成真正 parser-inserted 的 script 标签，保留 defer/async/id/onload 等属性。不通过 setTimeout、DOMContentLoaded 回调或 eval 模拟执行时序，不在服务器执行 JS/构建用户代码。包内模块依赖图仍明确拒绝；外部已审核脚本继续原生 URL，不从服务器下载。

版本 3 的冻结 UI metadata 明确声明固定本地资源来源 data:，只为自包含脚本服务；管理员输入的 AppCSP 仍只接受明确 HTTPS origin，不开放任意 scheme 或 sandbox。预览模型把这一固定本地能力与外部网络名单分开：只传 local_script_sources=["data:"]，JS 层也只接受这一固定值；不把 data: 当成外部域名显示/授权，不接受 blob:/任意 URL/重复值。mock/real 的 connect-src、frame-src、设备限制仍不变。旧 html-assembly-1/2 保持各自原装配分支和原 CSP，不在读取时改成 version 3 或重写包。

新增 5 项经典脚本字节/标志/SRI/模块拒绝回归，补充旧冻结 version 2 等价回归、正式 preview API 的本地 data: 声明与空连接域名断言；Flutter 增加本地来源不伪装为网络授权测试，Node 增加固定 local-source 白名单与网络仍关闭测试。全量后端 443 项（12 个既有警告）、Flutter 85 项、Node 11 项通过，相关静态分析、Web release/Wasm dry-run、格式/导入/diff/OpenSpec strict 通过。

真实浏览器普通页夹具 timing 结果为 order=one,two / interactive=true / late=true / global=true / dcl=true / async=true / onload=true / id=defer-one，验证顺序、DOM 时机、全局作用域及 currentScript。再用临时实例正式 Flutter release 管理页打开已发布 native-timing 的默认模拟预览，双层 opaque iframe 中实际得到 order=one,two; dcl=true; late=true; async=true，没有开启真实工具或外部网络；关闭后 iframe 移除。夹具使用临时 SQLite/配置/包及无生产权限身份，未调用用户 MCP；临时服务和标签页均已清理。未覆盖后端部署静态目录或升级/重启用户实例。

模块依赖图与其他 HTML/Host 验收仍未完成，9.6 保持未勾选；本轮原生调度证据不替代完整上传 Apps 或第三方 Host 验收。

## HTML 响应式图片装配与冻结版本兼容

9.6 继续实施：新增 `app_srcset_assets.py`，依据 [HTML srcset 解析模型](https://html.spec.whatwg.org/multipage/images.html#parse-a-srcset-attribute) 识别候选 URL/描述符，不按逗号直接拆分 data URI。支持 img/source 的 srcset、link 的 imagesrcset，保留 sizes/imagesizes；密度 x、宽度 w 及合法 w/h 组合保持原描述，浏览器决定实际图片。每个候选均复用包内路径、审核外部 origin 和派生容量检查，限制 512 个候选，拒绝缺失附件、穿越、未审核来源、无效/非有限描述符，不下载或执行内容，不更改原 ZIP。

为保护已有发布结果，新草稿采用 `html-assembly-2`，旧冻结 `html-assembly-1` 继续原分支（旧 imagesrcset 不被新逻辑改写）；已发布但历史缺失冻结版本的记录按旧装配读取，不在读取时升级。未知装配版本明确拒绝。新版本发布冻结新的 HTML/metadata 摘要，原始下载仍为上传字节。无需迁移/重写旧记录或包；需要新装配能力时创建新版本审核。

新增 18 项回归覆盖密度/宽度、data URI 内部逗号、picture/preload 属性、容量数量/描述符/来源/穿越、新发布 version=2、原 ZIP 下载，以及旧 version=1 有/无冻结摘要的字节等价、未知版本拒绝。完整后端 437 项通过（12 个既有警告），相关格式/导入/diff 与 OpenSpec strict 通过。真实 IAB 浏览器访问 loopback 夹具 `tests/browser/app_html_fixture.py?case=responsive`，密度和宽度两张图均报告 loaded=true / data=true；复验 ordered 仍为 first,second,third / 绿色样式 / third-script，raw 仍显示原中文、结束标签和 emoji。仅临时静态夹具，未访问用户配置/数据库，服务和标签页已清理。

模块分块、defer/async、完整嵌套框架及其他 HTML/Host 矩阵仍待实现/验收，因此 9.6 不勾选，不以本轮 srcset 进展替代完整 HTML 支持。没有构建同步后端静态文件、升级或重启用户运行实例。

## 上游 Apps 原生代理证据

完成 9.5：复用现有 FastMCPProxy/ProxyClient、公开 Client 的完整 MCP result API，无须额外 HTML 字符串替换或第二套 Apps 转换器。新增 `tests/test_upstream_apps_proxy.py` 四组回归（HTML/Prefab × auto/legacy），通过真实 MCP HTTP 序列化和内存 ASGITransport，将同名两个上游分别接入实际 MCPServerFactory。完整比较 tools/resources 描述、UI MIME/CSP/权限/visibility、自定义 metadata、文本与 structuredContent、逐 resource content metadata、自定义 MIME 二进制 blob，以及框架结果的其他字段；打开 URI/HTML/官方 renderer 保持原值。两个来源使用相同 ui:// 与 save 名称，保存仅改变所连接来源，后续打开取得新动态数据而不是冻结快照。

现代响应的标准 `io.modelcontextprotocol/serverInfo` 按固定 4.1.0 实际语义单独断言：工具结果保留原来源标识，Resources 响应显示当前代理标识；不是把所有 metadata 都删掉后比较，其余应用字段逐项一致。旧模式无需此差异处理。此证据覆盖代理数据链，不代表第三方 Host 已渲染，也不代替 SSE/STDIO/跨协议全矩阵或 Catalog 聚合资源/回调路由（9.8）。

同时核对完成 1.8 最小原型：固定 prefab-ui/renderer 0.20.2 与官方 ext-apps SDK 2.0.3；重新运行 `scripts/probe_fastmcp_apps.py`，直接/公开代理的 CDN renderer URI、471 字节原文及 SHA 相同，定义和普通回调正常。结合已有 bundled renderer 的正式 Flutter 浏览器动态状态/真实调用、经典 HTML/JS/CSS/附件浏览器装配及官方 bridge 初始化/模拟/确认/取消记录，原型所需链路已具备。Node 10 项复验通过。原型不宣称模块分块/defer/async/srcset 等上传 HTML 全能力已完成，9.6 及真实 Host/部署恢复 9.13 仍未勾选；没有改动运行实例或已有包。

本轮全量后端 419 项通过（12 个既有警告），专项 4 项额外断言实际协商版本为 auto 2026-07-28 / legacy 2025-11-25；格式/导入/diff 与 OpenSpec strict 校验通过。未部署静态文件或重启用户服务。

## Apps 绑定鉴权与审计闭环

完成 9.4 的来源/权限/审计验证。绑定始终固定 App 版本的 alias、服务、原工具名和持久来源 UUID/Schema 摘要，调用前及分发前复核发布记录、当前 Key、来源运行代际及当前目录；调用完成后仍检查授权变化。结合既有同名多来源、缺失/重复别名、Schema 变化、删除后重建、服务停止、Key 撤销、发布撤销、匿名 App 与私有来源边界回归，不将 visibility 当权限。

新增 `app_audit.py`，REST preview-call 和 MCP Apps runtime 均按当前请求取得非秘密 `key:<本地记录 ID>`，不使用 Key 片段/无盐摘要/显示名称/飞书资料。匿名标为 anonymous，平台内部测试调用标为 internal，无法识别或辅助读取失败标为 unknown；回调输出只接受固定格式，不允许注入秘密或换行。审计新增真实原工具名，并把工具执行后授权改变从「成功」改为 authorization_changed_after_execution，保留部分操作可能完成且不重试的对外诊断。不更改数据库、Key 权限或旧 REST/CLI 契约。

新增 3 项审计回归覆盖真实 REST 与 MCP 两种调用路径、READ/WRITE 不同身份、参数/结果/凭证不落日志、失效/错误标识回调。新增 2 项上游认证回归使用真实 Apps HTTP/ProxyClient/上游 MCP 序列化链，仅将公开 HTTP client factory 接入内存 ASGITransport，不依赖外网：静态 Authorization 与配置 OAuth access token 都正确传达，捕获的所有上游请求不含入站 Mcpcat-Key；上游随后返回 403 时第二次调用以 isError 失败，业务只执行第一次，不回退别名或重试。此证据是内存 ASGI HTTP，不冒充真实 TCP、新旧客户端/协议矩阵或 OAuth 刷新验收；相应 Gate 仍待完成。

本轮全量后端 415 项通过（12 个既有警告），相关格式/导入/diff 与 OpenSpec strict 校验通过。没有改动 Flutter、部署静态文件或用户运行数据，也未重启用户实例。

## Apps 前端浏览基础（部分实施）

### 官方 Host bridge 与隔离预览

审核策略的真实浏览器矩阵现已通过，完成 9.11。可复验的 loopback 夹具为 `frontend/apps-host/test/browser-policy-{host,app}.html`，用固定编译模块创建与正式预览相同的双层 opaque iframe，不使用用户配置或凭证。模拟模式即使收到域名名单仍产生 connect-src/img-src 违规；真实工具模式未额外授权网络时同样离线。显式授权测试域名后，`https://example.com/` 的无凭证 fetch 实际完成，GitHub 公共图标实际加载，未审核 `https://example.org/` 继续被 connect-src 拦截。重新打开模拟模式后，同一资源再次被 CSP 拦截，证明不是全局放开或保留旧授权。最初 Google 图标受跨域策略影响不能作为成功证据，已改用允许匿名跨域加载的公共 GitHub 图标，不绕过浏览器限制。

授权网络也未扩大 DOM/storage、设备或导航权限：页面实际报告 parent DOM/storage 拒绝、camera/microphone/geolocation/clipboard-write 均 denied；尝试把内框架导航到已审核 example.com，浏览器实际显示「该内容被屏蔽了」，审核 connect/resource 不变成 frame 导航许可。关闭后 frames=0。

`browser-message-host.html` 用官方 App/AppBridge 的合法 save 请求建立正向内存计数 calls=1，再由另一个 opaque 兄弟 iframe 向父页及预览代理伪造同名 tools/call 和错误 nonce 装载消息，calls 仍为 1、原会话内容不变；关闭后再次伪造，calls 仍为 1、active frames=0。此处 callback 只改夹具计数，不调用真实 MCP，不接触业务数据。新增 Node 回归通过官方协议解析器验证数组/null/字符串参数以及未声明的 openLink/message/modelContext 请求被拒绝，计数不变；循环载荷和第 10001 条会话消息被 guard 拦截。Node 共 10 项通过，结合既有参数 512 KiB/消息 1 MiB/身份撤销/确认晚到/实际 HTTP 断开清理回归，形成 9.11 证据；不是第三方 Host、上游代理或完整 Gate Apps 结论。临时服务和标签页已关闭，原始包/数据库/运行实例未修改。

新增审核域名与预览实际权限分离：AppCSP 四类 origin 都拒绝控制字符、引号/HTML/CSP 注入、URL 编码与空 query/fragment、非法 DNS/端口；合法 ASCII/punycode 与 IPv6 origin 保留。普通远程资源 metadata 仍由官方模型序列化，声明不是 Host 自动授权。参照 [官方 Apps 安全模型](https://modelcontextprotocol.io/extensions/apps/overview)，预览不自动授予 camera/microphone/geolocation/clipboard、frameDomains 或 baseUriDomains，父页面明确列出未支持申请。

前端独立 AppPreviewPolicy/AppPreviewSession 将审核 connect/resource 域名显示在父页面。模拟模式始终离线；只有已发布的真实测试可以另行开启「允许本次预览访问上述外部域名」，明确提示可能发送界面数据、加载外部脚本。默认关闭，策略变化创建新 iframe/工具会话，关闭或版本/模式变化不保留授权。JS 再次校验有界 origin，拒绝父管理页 origin、凭证及指令注入，只扩大审核 connect 与资源指令；frame/object/base/form 和设备能力仍禁止，原 source/window/nonce/会话/消息上限不变。仅发送 connect/resource 名单与审核标志，不把全部 requested_metadata、来源认证或其他权限交给 iframe。

Flutter 规范指导本次拆出独立不可变模型和局部授权组件，沿用现有 Provider、主题和窄屏布局。新增后端 16 项 origin 用例、Flutter 3 项模型/360px/会话重建用例及 Node 3 项策略用例；全量后端 410 项、Flutter 84 项、Node 8 项、相关静态分析与临时 Web release/Wasm dry-run 通过。外部网络真实浏览器放行/拦截及完整 Host capability 矩阵尚未验收，因此 9.11 保持未完成；没有覆盖后端静态目录或重启/升级用户实例。

正式 Flutter release 页面真实工具联调已通过，完成任务 9.10。复用 loopback 临时夹具，新增只在内存运行的 store/slow 工具及公开测试计数页；所有数据库、配置、App 制品及测试身份均为临时数据，不启动用户 MCP 或使用生产凭证。模拟点击后 calls=0；主动开启真实测试，父管理页确认框实际显示固定版本、来源 preview-fixture、工具/别名及 value=7；取消后仍为 0，明确确认后 calls=1，Prefab 动态内容显示「写入结果：7」。

真实 HTTP 联调发现关闭后的断开轮询缺口：Starlette BaseHTTPMiddleware 的接收通道可能先检查取消，导致 is_disconnected() 的即时取消探测一直读不到已就绪的断开。已改为在请求体解析后阻塞等待 ASGI receive 的 http.disconnect，保留有界清理。重新运行同一 Web release，slow_started=1，关闭后 slow_cleaned=1；父页恢复默认模拟，iframe 销毁。取消并不保证撤销远端副作用，也不对不合作工具承诺强制终止。

新增两项回归分别覆盖接收前让出调度、完整应用认证中间件链收到 http.disconnect（不取消外层任务），相关取消/调用共 10 项、全量后端 394 项通过，格式/diff/OpenSpec strict 通过。本次不修改 Flutter 产物、不覆盖后端静态目录、不升级或重启用户实例；临时服务及浏览器标签页已清理。审核外部域名/Host capabilities 的完整安全矩阵、第三方真实 Apps Host、部署和恢复 Gate 仍待验收，9.11/9.13 不据此完成。以下保留此前阶段记录。

真实模式前端现已接线但尚待完整浏览器验收：已发布版本可在关闭预览时主动选择真实测试，打开后不允许中途切换；关闭/版本或状态变化恢复模拟。静态 preview 响应新增 real_callable 与不含凭证的工具来源标签，父页按当前快照和 allowed_tools 交集校验调用，JSON 参数复制并限制 512 KiB。每次调用（包括只读）都在父管理页显示固定版本、来源/工具/别名及完整参数，主动确认才发送 confirmed=true；不允许并发排队弹窗，不自动重放。

独立 AppPreviewCalls 控制器在确认前后及结果返回时检查会话/当前身份；确认等待期间关闭或换身份不发送，已发请求使用 Dio CancelToken 关闭取消。Web surface 清理确认 route、controller、bridge 和 iframe；认证状态变化关闭通道，实际调用仍由后端逐次复核。只把公开结果 JSON 返回官方 bridge，Key 始终留在父页 ApiClient，来源标签不序列化到 HTML。官方 bridge 将真实调用交给受信任父回调，模拟工厂强制模拟，即使误传真实回调也不使用；关闭与未绑定工具均拒绝。

新增 5 项控制器、1 项模式开关、1 项 API 契约测试及 2 项 Node 真实/模拟分离测试。全量 Flutter 81 项、Node 5 项、相关静态分析与 Web release/Wasm dry-run 通过。父页确认/取消的真实浏览器全链路仍待下一步验证，9.10/9.11 继续未勾选，未构建同步或重启用户服务。

真实 preview-call 现由 request-scoped 取消适配器持有：请求已断开则不创建操作，活动调用同时检查客户端断开；父请求取消或连接关闭后取消调用/检测任务，清理最多等待 1 秒。检测循环显式 stop event，避免 is_disconnected 内部的短取消作用域吞掉一次取消后继续轮询。对不合作任务保留引用与异常读取直到实际结束，不让失去消费者的异常悬挂；有界退出不代表远端工具已停止，更不保证副作用回滚，不自动重试。

新增 4 项取消回归（包括参数化父取消）覆盖未开始、协作清理、不合作任务有界返回与最终回收；另通过完整应用 ASGI 管理请求取消，验证信号进入绑定异步 MCP 工具并执行 finally 收尾。相关取消/真实调用共 8 项、全量后端 392 项通过，导入/diff/OpenSpec strict 通过。该证据不是浏览器 TCP 断开或前端 CancelToken 已接线，后续父页面确认和取消适配仍待完成，9.10/9.11 不提前勾选。

新增真实测试 API `POST /api/apps/{slug}/versions/{version}/preview-call`：当前只接受已审核发布版本及其公开回调名，草稿保持模拟预览；strict boolean confirmed 缺失/为 false 时拒绝执行，名称不能是任意原来源工具或其他版本别名。确认不代替权限，入口按普通 MCP/Catalog call 的 READ 身份调用，其他 Apps 管理写入权限不变。参数必须为有限 JSON 且不超过 512 KiB，先复用预览装配验证制品/派生摘要与绑定，再通过现有 call_app_tool 的逐次来源/Schema/身份检查及审计执行。不自动重试，原 MCP typed content/structuredContent/metadata 以 alias JSON 返回，不额外下发凭证。

新增 3 项真实管理 HTTP 测试覆盖确认缺失/类型不正确、直猜来源名、READ 正常结果、停用来源、参数大小、撤销版本及调用产生副作用后 Key 撤销；最后一例只执行一次并返回部分完成诊断。专项 3 项、全量后端 387 项通过，格式/导入/diff/OpenSpec strict 通过。服务端确认字段是调用者意图声明，不是额外认证凭证，前端必须在父管理页主动开启真实模式并逐次收集确认，上传 iframe 不持有 Key。前端确认适配和关闭取消仍待实现，9.10/9.11 继续未完成。

完整正式 Web/API 浏览器联调现已验证：临时实例使用独立 SQLite/配置/制品和无生产权限夹具身份，仅监听 loopback，不启动用户 MCP，不覆盖后端静态目录。用真实 release 构建进入草稿 App 详情，官方 Prefab 0.20.2 renderer 在双层 iframe 中显示初始计数 1，键盘触发声明式 setState 后为 2；关闭后实际 iframe 数为 0，重开恢复为 1，无旧状态残留。服务日志只有预览/文件/接入查询，没有真实工具调用。

本轮还按 Flutter 规范同步 Host 主题：管理页亮度只转换为 light/dark，使用官方 hostContext 初始化/更新通知，不修改已冻结 renderer HTML 或上传包。暗色管理页面中的 Prefab 已实测渲染暗色，Node 用例覆盖初始 dark 和切换 light。全量 Flutter 74 项、Node 3 项、相关静态分析与 Web release/Wasm dry-run 通过。可复用联调服务为 tests/browser/app_flutter_fixture.py，所有数据置于 TemporaryDirectory，验收后临时服务和标签页已清理；该证据不代替第三方 Host、真实工具/审核域名或完整部署恢复验收，9.10/9.11/9.13 仍未完成。

后续已接入 Apps 详情的主动模拟预览卡片，固定版本 API 经独立 Repository/AppPreview 模型加载，只把 mode/html/initial_result/allowed_tools 四字段传给自有 JS adapter。普通文件浏览仍不执行内容；跨版本或非 mock 响应拒绝、不回退 latest。关闭或版本/状态改变会销毁 Web surface，异步装载晚到后也关闭会话；立即失败和关闭后的晚失败均有错误监听，不暴露原始异常。非 Web 平台明确提示使用 Web 管理界面。

JS adapter 等待 Flutter platform view 实际加入 DOM 后再创建双层 iframe，避免 onElementCreated 早于 DOM 挂载导致 contentWindow 为空。模块通过明确 ready Promise 加载；自身代码构建只使用锁定依赖，生成 web/apps-preview.js，现有工作空间 build_web.sh 已增加 bridge 构建阶段。这里只修改脚本，没有执行覆盖后端静态目录或重启服务。

新增 4 项模型/Widget 和 1 项 API 测试覆盖主动加载、慢结果关闭、失败重试、切换版本、Host 参数白名单及跨版本响应拒绝。全量 Flutter 74 项、Node 3 项通过，相关静态分析无问题；Web release 编译及 Wasm dry-run 已通过并输出临时目录。正式页面的真实浏览器联调、真实工具模式/确认及权限撤销仍待补齐，9.10/9.11 不因页面接线而完成。

新增 `GET /api/apps/{slug}/versions/{version}/preview`，只返回 application/json 的 mock 预览装配数据（HTML、初始 structured content、固定版本公开回调名单及审核 metadata/摘要），不把上传 HTML 作为同源页面响应。复用原制品读取和 render_app，不执行上传 JS/Python、不构建用户代码、不调用真实工具，不传入网关 Key 或来源认证信息。支持 WRITE 草稿与 READ 已发布版本，原始下载字节不变；远程端点无需为了管理预览而自动开启。

装配前后检查当前 Key、绑定来源及 Schema、制品完整性和配置快照；配置在耗时装配中变化或 Key 撤销时拒绝返回 HTML，不用旧权限延长预览。对来源或制品不可用返回标准错误，不回显宿主路径或上游异常正文。新增五项真实管理 HTTP 用例覆盖草稿不可被 READ 读取、JSON/私有缓存/原字节、规范 Prefab 回调映射、业务工具调用计数为零、来源停用、装配中配置改变、Key 撤销及损坏摘要。专项 5 项、全量后端 384 项通过，格式/导入/diff/OpenSpec strict 通过。Flutter 页面接线和真实工具模式尚未完成，9.10/9.11 保持未完成；未升级/重启用户实例。

后续真实浏览器测试发现并修复单层 iframe 的自身导航缺口：仅 connect-src none 拒绝 fetch，但上传页面可以导航自己发起请求。使用临时本机夹具实测，旧结构产生 1 次 `/probe` 请求。现改为受信任外层 opaque proxy + 上传内容内层 opaque iframe；外层 CSP frame-src 仅 about:，内层仍禁用嵌套 frame/网络，父嵌入策略约束内层导航。proxy 仅接受精确 parent window/origin 加随机 nonce 的首次 HTML 装载，协议消息来源精确绑定到内层窗口，限制消息大小；不授予 same-origin、top-navigation 或设备权限。

Codex IAB 真实浏览器复验：官方 App/Bridge 初始化、input/result、允许工具的模拟结果、未绑定工具拒绝均正常；parent DOM/localStorage/fetch 被拒绝。固定初始数据触发相同自身导航后浏览器显示 CSP 屏蔽，服务端探测数为 0；正常模式重开后仍可模拟调用，关闭后外层及其内层销毁，页面证据为 network probes 0 / frames 0。临时服务仅监听 127.0.0.1:18764，不读取用户数据库或 Key，验收后已停止并关闭标签页。

复用夹具位于 `frontend/apps-host/test/browser-{server.js,host.html,app.js}`；先 npm run build，再 node test/browser-server.js，可通过页面按钮重验。Node 单元测试现 3 项通过（新增 proxy 静态边界回归），ESM 打包约 1011 KiB。此为管理预览模块的真实浏览器证据，不是 Flutter 页面已接入或第三方 Apps Host 兼容矩阵；真实工具/审核域名/完整生命周期安全测试仍待完成，9.10/9.11 不提前勾选。

在前端独立 `apps-host` 私有构建目录固定 `@modelcontextprotocol/ext-apps=2.0.3`、`esbuild=0.25.12`，保留 npm lock 和 SDK 包完整性摘要（SHA-512 `Tn+4+cyhO4f4cQSDxNyLiIfEE+qlTTuIbWBJB8JTinEXOhDYgCT4wDTQplxD9MBYZH5pCYGC59GaxiwIT4bu8w==`），安装使用 ignore-scripts。参考 [官方 AppBridge 源码](https://github.com/modelcontextprotocol/ext-apps/blob/main/src/app-bridge.ts)，使用公开 AppBridge(null, ...) 与 PostMessageTransport，不自行实现协议握手、不传入网关客户端或凭证。模拟工具仅允许显式名单，不发真实 HTTP 请求；输入通知先于初始结果，关闭后不再接受模拟调用。

新增 opaque-origin `sandbox=allow-scripts` iframe 装载模块，不授予 allow-same-origin、表单、导航或 Host 设备能力。模拟模式 CSP 拒绝 connect/frame/object/base/form 外部访问，仅允许包内内联脚本/样式和受限 data/blob 资产；脚本仅在客户端 iframe 内运行。官方 transport 外加精确窗口来源、null origin、1 MiB 消息、10000 次会话消息及关闭检查，关闭请求给官方 teardown 250ms 后释放 bridge/transport/frame。真实工具模式尚未实现，不通过此模块偷偷开启。

两个 Node 测试通过：官方 App/AppBridge 双向初始化、输入/结果顺序、允许模拟工具/未绑定拒绝，以及窗口/origin/载荷/关闭边界。浏览器 ESM 打包成功（约 1008 KiB），生成物和 node_modules 忽略，不修改现有部署。该模块还未接线 Flutter、未做实际浏览器 sandbox/真实 Host 验收，1.8/9.10/9.11 继续未完成；与此前 Prefab Python renderer 0.20.2 的锁定独立。

远程接入卡片现已接线，固定 slug/版本/状态变化重新加载接入检查，旧慢请求不覆盖新选择，只显示服务端给出的当前版本地址/打开工具名/UI URI。无凭证复制、READ Key 占位符、协议握手后 tools/call 示例和实际 Host 支持提示；默认不承诺所有 Agent 自动渲染。客户端再次拒绝 userinfo/query/fragment/非 HTTP 地址、控制字符、不完整或跨版本入口，错误提示不回显原始网络异常，不读取管理员设置或自动启用 Apps。

新增 4 项模型/Widget 与 1 项 Repository 接入测试，覆盖 360px、复制占位符、固定版本消息、慢请求切换、错误重查、不安全地址/跨版本 URI 和只读 API。全量 Flutter 69 项通过、相关分析无问题。结合上述上传、版本内容、绑定配置、发布撤销、原始下载及真实管理 API 测试，任务 9.9 完成。9.10–9.13 的隔离预览、Host/安全及实际部署验收不据此完成；未构建同步、未重启用户实例。

新增只读 `/api/apps/{slug}/connections`，按当前身份可见的明确版本返回运行状态。仅在实际 Apps runtime 存活、配置路径匹配、原始内容/派生摘要及绑定来源校验通过时，提供 MCP 地址、固定版本 UI URI 和真实 `open_tool`。复用 Registry/renderer/工具命名公开实现，不另外猜测名字，不读取用户脚本，不在查询时创建实例或端点身份。耗时校验后复核当前 READ 身份。

连接地址优先配置的公网基础地址，否则当前请求地址；含 userinfo/query/fragment 的异常地址不下发。草稿、撤销、端点未挂载、来源停用或摘要漂移均不提供可复制资源入口。免认证端点加公开 App 不代表私有来源工具免认证，提示同时考虑绑定来源。新增 3 项真实管理 HTTP 回归，与实际 MCP tools/list 比较工具名/URI，覆盖撤销、停止、摘要漂移、存活状态、秘密地址与私有缓存；全量后端 379 项通过，针对性 3 项通过。前端接入卡片尚待接线，9.9 仍未勾选，不声称真实 Host 验收完成。

版本详情后续加入草稿工具绑定、CSP/Host 能力/变更说明完整替换编辑、明确审核发布以及已发布版本撤销。配置采用保留完整原值的 JSON 编辑，绑定显式包含 alias/server_name/tool_name，空列表撤回全部授权；服务端仍验证来源与实时权限，不将 UI 校验视为授权。READ 用户可查看配置但没有管理按钮，已发布版本仅允许撤销，不原地修改内容或绑定。

发布前必须确认审核；公开选择明确影响同一 App 的全部已发布版本，改变选择清除确认。发布/保存/撤销等待期间锁住动作，失败不自动重试、提示刷新核对。刷新详情保留本次操作的明确版本选择，不悄悄切到另一历史版本。新增 4 项 Widget 与 1 项 Repository 契约测试覆盖确认/范围变更、慢请求、空绑定、撤销、READ 可见性、配置完整保留及独立明确版本 API。全量 Flutter 64 项通过，相关分析无问题；远程接入和隔离预览尚未完成，9.9 仍未勾选，未构建或重启用户实例。

后续新增 WRITE 用户可见的静态 App 上传对话框，支持 Prefab JSON、单 HTML、已构建 ZIP 的文件快照、slug/名称/明确版本及可选入口路径。客户端拒绝空包或超过 16 MiB，实际包内容仍由服务端校验。multipart 使用现有认证客户端及长操作超时，只调用 `/api/apps/upload` 创建草稿，不自动调用绑定或发布。等待期间禁用编辑、关闭和重复提交；成功按本次上传版本显示摘要，不误取响应中排序更高的历史版本。失败保留编辑/文件，不自动重试，超时提醒先检查版本是否已创建。

新增 3 项上传 Widget 和 2 项 Repository 测试，覆盖 360px、慢请求锁定、失败恢复、READ 禁止、原字节快照、独立 API 路径、multipart 字段/长超时和明确版本文件/下载。全量 Flutter 59 项通过，相关源码/测试静态分析无问题；未构建、未重启，任务 9.9 仍保留未完成。

新增 MCP Apps 菜单、独立 `/apps` 列表及 `/apps/:slug` 详情。使用独立 Apps Repository 和现有认证客户端，详情按服务端排序选择明确版本，切换版本重新加载文件，不借用其他版本的内容。复用文件阅读器的 Markdown/源码能力；HTML/JS 仅展示文本，不在普通详情页执行。原始制品下载使用明确版本、匹配 ZIP/JSON/HTML 扩展名与 MIME，慢下载锁住按钮和版本选择，失败提示允许重试。侧边导航可滚动，防止新增入口挤出窄高视口。

功能关闭时明确提示联系管理员，不自动启用环境开关；权限错误不回显原始网络信息。新增三项 Widget 回归覆盖关闭/重试、权限、360px 版本切换及 HTML 源码。全量 Flutter 54 项通过；相关 Apps 源码静态分析无问题。Flutter 规范指导网络逻辑独立在 Provider/Repository、沿用项目主题、窄屏 Wrap 与已有安全文件阅读器，不新增框架。

上传、绑定、审核发布、停用、远程接入指引和隔离 Host 预览仍未完成，因此任务 9.9 不勾选。未构建同步 Web、未升级或重启用户服务，也不将 Widget 测试当作真实 Apps Host 验收。

## 已实现的基础

- FastMCP 固定 4.1.0，安装 apps/code-mode extras；Prefab 固定 0.20.2，Monty 固定 1.1.0，依赖锁已更新。容器保持 Python 3.12，并增加依赖版本安装检查；尚未执行完整 Docker 镜像验收。
- 传给 FastMCP 的 OpenAPI client 与 Inspector Auth 使用 httpx2。应用自己的 REST HTTP 库继续保留 httpx。
- STDIO/SSE/HTTP 代理每次创建独立客户端；只使用配置的上游 headers/OAuth，不默认转发入站网关凭证。旧请求选 legacy，其他请求由框架 auto 探测；交互回调跨协议时代仍需单独验证。
- Inspector 保留应用级 session、工具 Schema 和现有 REST 字段，使用当前配置的认证头。
- `MCPCAT_MCP_HTTP_SESSION_IDLE_TIMEOUT=0` 保留旧 HTTP 无空闲过期的默认行为；正数设置秒数。有限超时到期的客户端重连仍需完整验收。

## 新数据表

Alembic `0003_apps_registry` 新增 Apps 身份、版本、原始制品和工具绑定。`0004_skill_content_variants` 新增 Skill local/remote 变体、remote 制品和独立运行身份表。

旧 `skill_artifacts` 的版本唯一约束不变，迁移只增加 local 引用，不重新压缩旧 ZIP，不自动生成或发布 remote。相同内容可以共享制品。冻结版本不能直接增加变体，需新版本。

稳定 UUID 保存到 `runtime_identities`，不放入会被旧配置导入/覆盖的 `system_settings`。回归测试验证原配置导入和默认实例初始化行为。

迁移使用现有 SQLite backup 机制并显式开启 SQLite DDL 事务。注入新增表中断后，新增表和 revision 回滚，重试成功。完整升级/恢复演练及历史大数据矩阵仍未完成，不应直接用降级依赖恢复新版数据库。

## Apps 上传基础

已实现纯静态 Prefab JSON、HTML 和 ZIP 校验/存储。原文与逐文件 SHA 保存；路径穿越、特殊文件、加密包、容量/数量超限、非法 Prefab 结构被拒绝。上传的 Python 附件不会被导入或执行。默认 `MCPCAT_APPS_ENABLED=false`。

这些函数尚未构成可用的 Apps 中心：管理 API、发布授权、MCP 分发、前端界面、CSP/隔离 bridge 和真实 Host 交互还在待办列表。不得在此阶段建议用户开启并作为生产 Apps 服务使用。

Prefab `from_json()` 并非完整校验接口，平台补充结构与官方组件模型验证。官方 renderer 支持 CDN 和 bundled 模式，后者测试为约 6.6 MB 自包含 HTML；正式发布需使 renderer 模式与审核 CSP 匹配。UI-only metadata 不是权限控制。

## Skill 变体管理与兼容边界

新增管理接口使用 `/api/skill-variants`，不向旧 `/api/skills` 和 bootstrap 响应加入变体结构。上传支持 `local`、`remote`、`both`，默认 `local`；明确版本的详情、文件预览和下载始终选择指定 profile/binding，不跨端点回退。Markdown 返回可渲染原文；二进制和超出预览限制的文件返回原因，原制品仍可下载。

一个版本整批审核发布：先校验全部预期变体及 ZIP/逐文件摘要，再冻结集合。相同上传内容共享原 local 制品；为历史上传版本补 remote 时创建更高版本草稿，不重写旧包。MCP 生成 Skill 的 remote 内容仍需专用模板，不能机械复用 local 的 mcporter 指令。

remote-only 发布不推进 local latest，不进入旧版本列表，也不改变历史 bundle。撤销 remote 不影响同版本 local 下载；退役允许读取冻结历史，归档禁止公开读取。并发编辑与发布使用同一个版本锁，确保 ZIP、manifest 和来源快照一致。上传失败仅清理本次草稿与它拥有的制品，不删除历史共享内容；审计不保存正文、参数、凭证或原始异常消息。

远程匿名可见性默认关闭，需在发布时显式审核 `remote_public`。连接信息接口在实际 MCP 端点未注册时返回 `ready=false`，不把管理 API 可用当作官方 Skills 协议可用。后续协议层必须执行端点绑定、READ Key、当前发布状态和该匿名可见性检查。

上传的 Python/JavaScript/shell 仅作为原始附件验证、预览和读取，不导入或执行。测试同时覆盖 HTTP 文件预览和发布读取；没有把上传包交给 FileSystemProvider 或 CodeMode。

2026-10-15 隔离环境全量后端回归：157 项通过、12 个已有警告。其中新增变体工作流 15 项、上传恢复/生命周期/清单损坏 4 项；不代表真实 Host、旧公共 CLI、OAuth、Apps 交互和完整恢复演练已经验收。

## 官方 Skills 协议组件与独立网关端点

`RegistrySkillsExtension` 使用 FastMCP 公开 `ServerExtension`/`MethodBinding` 实现现代 `skills/list/get`，并检查客户端每请求 opt-in；`RegistrySkillResources` 使用公开 `Provider`/`Resource` 提供普通 Resources 读取。未实现可选目录读取，因此不声明 `directoryRead`。

资源 URI 为 `skill://mcpcat/<instance-uuid>/<endpoint-uuid>/<skill-id>/<encoded-version>/remote/<slug>/<file>`。解析只得到 Registry 键，不直接访问磁盘；拒绝跨实例/端点、未知文件、查询/fragment、编码斜线、穿越和非规范编码。公网地址不参与身份。get 和 read 使用固定版本，后续新发布不会替换旧 URI 的内容。

每个 entry 保留全部 frontmatter 和逐文件 SHA/size。UTF-8 文件保持 CRLF 等原始字节，二进制通过框架 blob 返回。正式资源读取不套用 512 KiB 预览限制；512 文件和 16 MiB 总大小的边界已验证。

分页以短时有界 URI 快照实现；游标绑定实例/端点/请求身份，每页重新检查发布与可用性。游标不包含正文或凭证，不作为授权；旧页不会因新版本发布混入新内容，已撤销内容仍被过滤。协议响应使用 `ttlMs=0`、`cacheScope=private`，没有服务端正文缓存。网关游标身份使用 Key 的有盐 HMAC；实际不同 READ/WRITE Key 不能共用游标。

针对性测试 `tests/test_remote_skill_protocol.py` 共 11 项通过，包括真实 ASGI 现代请求、完整分页、未知 URI 官方错误、逐文件 digest、撤销读取、匿名审核、跨来源隔离，以及 legacy 握手不声明扩展、原工具和普通资源仍可用。

本批变更后的全量后端回归：168 项通过、12 个已有警告。OpenSpec 严格校验通过；不代表生产挂载或真实 Host 验收已经完成。

独立端点已接入应用工厂，使用公开 HTTP lifespan，由父应用启动/关闭，默认 `/mcp/skills`。`skills_mcp.enabled=false` 为安全默认；缺失旧配置不会自动写入新段或开启远程服务。管理员 `GET/PUT /api/admin/settings/skills-mcp` 返回 config、ready 和 restart_required，部分更新保留未知字段。

运行中关闭开关或改变路径立即使旧入口失效；首次启用或更换路径/分页大小需重启，配置保存不等于端点已就绪。路径冲突检查覆盖已有服务、Catalog、Apps 和 CodeMode；关闭但未卸载的旧路由仍禁止动态新增服务占用。认证检查先按实际挂载的独立端点识别，避免仅凭未生效配置把同名用户服务当成公开服务。

每次读取重新校验当前 Key 的 READ/启用/过期状态；WRITE 仍不可通过协议读取草稿。公开端点只有显式审核 public_remote 的冻结内容允许匿名读。MCP HTTP/SSE 的正常、错误和流式响应均使用 `Cache-Control: private, no-store`，并对当前认证头 `Vary`，不缓冲正文或改写 SDK 的协议缓存语义。

真实应用工厂针对性测试覆盖上述开关/鉴权/冲突/缓存及自定义头、稳定身份重启、Catalog 关闭时读取本地脚本但不执行、管理员设置权限与原配置保持。独立端点接入不代表真实 Host 激活验收；实际用户数据库和正在运行的服务没有升级或重启。

本批 `tests/test_skills_mcp_gateway.py` 共 11 项通过；全量后端回归 179 项通过、12 个已有警告。OpenSpec 严格校验、相关文件格式检查与 `git diff --check` 通过。详见 [远程 Skills 使用边界](../user/remote-skills.md)。

## 服务/Catalog 绑定和来源生命周期

Manager 初次创建、动态新增和重启 MCP 时，在 HTTP 应用生成前注册当前来源的 Skills adapter；Catalog 在公开 FastMCP 实例创建时注册。服务仅提供其 UUID binding 的内容，Catalog 仅提供 Catalog binding；声明的 `required_tools` 必须全部存在于当前公开 Provider 的实际目录，缺失或目录读取失败时不提供 Skill。

每次读取采用当前 Key、当前 require_auth 和发布状态；来源端点公开不自动使 Skill 公开。公开服务的未经 remote_public 审核内容对匿名请求不可读，WRITE 同样不能读取草稿。管理连接信息也检查实时工具和制品，而不是只相信端点状态。

使用公开 `Middleware.on_discover` 在关闭/失效时移除平台自身 Skills 声明，原工具和 UI 声明不变。公开 `on_read_resource` 保证平台 `skill://mcpcat/` 内容从 Registry 验证读取，失败不回退上游；上游同名静态资源不能劫持已发布文件。未添加未知上游扩展的转发或声明机制。

来源停止/删除会隐藏内容，普通重启保留 binding 与旧 URI。配置删除与 runtime 身份退休在同一事务中完成，完整配置导入删除来源也适用；同名重建获得新 UUID，旧版变体不会自动关联新来源。读取退休 URI 不会重建身份。旧 local ZIP 不重写。

修正路径冲突保护：本 Manager 所拥有的旧 MCPProxyApp 路由允许同名服务重建，其他 Manager/系统端点仍视为冲突。独立 Skills 端点可以单独关闭而保留服务/Catalog 扩展。`tests/test_skills_source_gateway.py` 8 项通过，使用真实 Manager/lifespan 和真实 MCP 请求；MCP factory 替换为固定 FastMCP 测试工具，不启动外部用户进程。完整外部上游类型、OAuth、取消和 Gate C 仍需继续验收。

本批全量后端回归：187 项通过、12 个已有警告；OpenSpec 严格校验通过。当前提案 28/85，未达到发布条件。

## 可审阅生成模板

模板位于 `app/resources/skill_templates/`：local/remote × service/catalog 四份正文，以及共享授权边界和来源指令参考。`skill_template_renderer.py` 只做一次文本替换，不求值来源中的美元表达式或代码。description 由 `skill_descriptions.py` 统一生成，说明用途和触发场景；执行前提保留在 compatibility，工具 Schema 和上游说明置于按需参考。

local 继续携带关闭自动导入的 mcporter 配置、固定 0.13.7、Node >=24、环境认证占位符。remote 不携带配置、API Key 或 mcporter 命令，使用 Host 来源连接和实时 Schema；Catalog 说明先 search_tools 再 call_tool，搜索不等于副作用授权。两种内容返回相同用途描述，新模式管理 API 已接入批次保存；旧生成 API 默认 local 且不增加响应字段。

新模板字节变化使生成器独立版本升为 1.1.0；应用/CLI 仍为 1.0.1，最低 CLI 不变，兼容矩阵和 Gate C 文档校验同步。local 去重上下文加入模板摘要、版本、认证头和用途/来源说明摘要，模板或说明变化产生新草稿，不覆盖冻结 ZIP。模板已验证随 Python wheel 打包，避免部署后文件缺失。

`tests/test_skill_templates.py` 5 项通过：两模式用途一致/执行前提不同、相对参考、秘密不泄露、来源模板片段不求值，以及冻结历史字节不变/新上下文幂等。原生成器 18 项与全量后端 192 项通过（12 个已有警告）。`skill-creator` quick_validate 的旧字段白名单拒绝 compatibility；保留 [官方支持的 compatibility](https://agentskills.io/specification)，项目 ZIP 校验器验证通过，不修改全局 skill 或删除兼容前提来迁就该脚本。

## 同快照生成批次

`POST /api/skill-variants/generate/service/{name}` 与 `POST /api/skill-variants/generate/catalog` 接受 selection=local/remote/both，默认 local。新版接口与旧 `/api/skills` 共用 `skill_generation_batch`，旧响应不增加模式结构。remote 不解析 public_base_url，不受访问地址/认证头变化驱动；both 同时从一次已规范化工具快照生成，先校验完整批次，再创建待审核草稿。

模式集合、来源 UUID、工具 Schema、生成器版本与每模式实际 ZIP SHA 组成幂等键。相同上下文返回原版本；新增模式或内容变化创建更高版本，不修改冻结集合。仅 local 地址变化时，remote 字节仍相同并共享原冻结制品。历史匹配制品必须通过实际读取/完整性校验，不能把缺失文件当作 unchanged 成功。

按 slug 串行化重复生成，按版本串行化存储和发布；文件/元数据批次不会被半途发布。失败删除本次草稿和它拥有的文件，保留历史共享引用，仅记录异常类型。生成快照前固定来源身份，来源在扫描或提交期间重启/删除重建时拒绝旧快照，避免旧内容误绑定新来源。打包和数据库工作放线程池，工具快照仍由公开异步 Provider 读取。

服务 remote metadata 绑定实际工具；Catalog 绑定 search_tools/call_tool。用户上传的 remote 保留本地脚本及 compatibility，通过明确上传/发布审核与同一文件分发，不调用脚本。此前上传、公开审核、HTTP/MCP 文件读取和非执行测试继续通过。

`tests/test_skill_generation_modes.py` 8 项通过：同快照/整批发布、remote-only/旧 local 字节、URL 改变共享 remote、并发幂等、失败恢复、Catalog 合成绑定、真实新 API 生成→发布→来源 MCP 读取→旧 ZIP 下载，以及扫描期间来源替换拒绝。全量后端 200 项通过、12 个已有警告；Gate C 文档一致性脚本通过，真实公共 CLI/Host 验收仍待完成。

## 前端模式入口（实施中）

前端沿用 Riverpod、现有 Material 主题和生成反馈。服务卡片、Catalog、Skill 详情刷新共同使用模式选择对话框，默认本地；远程说明强调客户端能力及客户本地脚本执行。取消不提交，生成开始后仍使用来源级锁和持续进度。local 调用旧接口，remote/both 调用新模式接口，保持独立长操作超时。

新增对话框窄屏/取消测试，并更新原生成反馈测试；全量 Flutter 22 项通过。新 API 测试断言最终 URL `/api/skill-variants/generate/...`，防止重复 `/api`。新增组件、API、Provider、Catalog/详情相关文件分析无问题；服务卡片其余原有 withOpacity/async context 提示未在本次扩展为全文件重构。

模式入口并非完整双模式 UI：remote-only 列表、明确版本/profile/binding 文件预览、远程接入信息、上传模式、权限/审核提示及部署 Web 同步还在实施。尚未构建到后端或改变正在运行的页面，7.1–7.7 保持未完成直到对应验收通过。

## 可重现的诊断

在已安装锁定依赖的隔离环境运行：

```bash
python -m pytest
python scripts/probe_fastmcp_v4.py
python scripts/probe_fastmcp_network.py
python scripts/probe_fastmcp_apps.py
PREFAB_BUNDLED_RENDERER=1 python scripts/probe_fastmcp_apps.py
```

network 原型仅绑定随机本机端口，退出时关闭。测试 mcporter 前先在临时 npm cache 安装指定版本，再设置 `PROBE_MCPORTER=1`；原型使用明确空配置，不读取用户现有 MCP 配置。

原型覆盖四个旧 HTTP 握手版本、现代 HTTP、SSE、mcporter 0.13.7、Prefab 数据/renderer 的直接和代理读取；UI 点击回调、OAuth、热切换/取消、完整安全和实际第三方 Host 支持矩阵仍不能据此宣称已通过。

## 发布前仍需完成

以工作空间 OpenSpec `upgrade-fastmcp-v4-and-official-skills/tasks.md` 为准，未勾选任务不能视为完成。尤其包括官方 Skills 一致性验收、旧 CLI 全流程、CodeMode 权限/限额、Apps 管理与隔离预览、Web 构建同步及恢复验收。

## CodeMode 配置与原始目录（尚未开放执行）

新增 `GET/PUT /api/admin/settings/code-mode`，强制 WRITE，默认关闭、路径 `code-mode`、始终需要 Key。保存有限默认值：Monty 计算 30 秒/100 MB，50 次工具尝试，每请求工具并发 4、每身份 execute 并发 2、工具等待 30 秒、总 deadline 120 秒，代码大小 64 KiB。这些是后续桥接层要实施的配置，不代表仅凭保存就已实施所有运行限制。时间/内存/次数/并发均拒绝 None、零、负数、无穷、NaN 或布尔假冒数值。

缺失旧配置只按关闭解释，不主动写回。管理员部分更新会持久化完整有限默认值，保留未知根字段及未修改的嵌套限制；API 响应只返回已定义字段，不回显未知字段中可能保存的凭证。固定依赖为 FastMCP 4.1.0 + pydantic-monty 1.1.0，版本缺失/漂移拒绝启用，不自动安装。启动及保存检查普通服务/Catalog/Skills/Apps 路径，已启用但待挂载的 CodeMode 路径不能被普通服务占用；实际旧路由归属也不会覆盖。

新 `CodeModeProvider` 复用 Search Transform 前的 CatalogProvider 原始工具、别名/Schema、纳入与 OAuth 排除规则。允许服务/工具配置为 None 表示不额外缩小范围，空列表明确允许零项。授权、范围和服务状态每次读取重新校验，查询期间撤销也不能返回旧目录。先沿用整个 Catalog 的歧义别名排除，再缩小结果，防止过滤一个来源后重新启用原目录已拒绝的冲突名称。Apps/UI-only 和具有网关组件标记的元工具被排除，普通上游叫 execute 的工具保留；网关自己的 CodeMode 端点不注册成用户来源。

此阶段没有创建/挂载 CodeMode execute 端点。状态不伪造 ready，已保存启用时明确返回 `endpoint_not_mounted`；在逐调用鉴权、受控 Monty bridge、等待/总 deadline、并发、审计与取消完成并验证前，不建议用户启用生产执行。官方 Transform 会重新取得目录并计数，但这不能替代桥接限额和当前 Key 校验；上传 Skill 文件仍不进入执行目录。

`test_code_mode_settings.py` 与 `test_code_mode_provider.py` 24 项通过，覆盖默认不改旧配置、有限参数、WRITE、部分持久化/未知秘密不回显、四类配置和残留路由冲突、待挂载路径保留、依赖漂移、加载服务前阻断、原始 Schema/同名来源、范围空列表、动态权限撤销、停止/关闭、Apps/元工具过滤及冲突不重启用。对应阶段全量后端 229 项通过、12 个已有警告；新模块 Black/isort 检查通过。任务 5.11/5.12 完成，Gate Code、5.13–5.18、CodeMode UI 和真实普通客户端验收仍待完成。

## CodeMode 受限执行桥接原型（仍未挂载 MCP）

新增 `CodeModeSandboxProvider` 实现公开 SandboxProvider 入口，始终创建官方 Monty 并传有限计算时间/内存。只允许官方 `call_tool` 外部函数，不传额外 inputs、文件、shell、网络或凭证能力。工具尝试先计数（包括无效参数/拒绝/失败/显式重试），每请求并发、工具等待和总 deadline 独立实施。总 deadline 包含工具目录查询及底层等待，使用兼容 Python 3.10 的 asyncio.wait_for，不将等待时间误当成 Monty 计算时间。

每执行重新取得当前配置/身份，保存来源持久身份与当前运行对象的双重快照。来源重启或同名重建后不能借用旧上下文；配置/Key 变化、超限后不再调度。客户端代码即使捕获限额异常，也不能把执行伪报成功。部分完成诊断不包含原始代码、参数、结果或上游异常文本，也不宣称已完成的写操作被回滚。

执行配额用共享数据库旁的 OS 文件锁，身份必须为 64 位安全摘要，独立锁对象防止同线程重入。原型的跨进程测试确认同一身份同一槽不可重复取得；生产 Runtime 接入还需要由持久私有盐生成稳定身份摘要。取消可取消任务并有界清理，未确认取消的底层请求保持执行槽，结束后才释放并记录 cleanup_finished；不会释放配额却让旧调用无限并发。执行审计为单行结构化日志，包含非秘密身份、请求 UUID、已解析的公开工具/来源、次数、状态和耗时，不记录代码、参数、结果或凭证。未知工具名也不把用户任意字符串写进审计。

`test_code_mode_sandbox.py` 19 项使用真实 Monty：组合计算、隐藏工具拒绝、Key 撤销及部分完成、捕获限额异常仍失败、工具/总超时、每身份隔离与取消释放、四类宿主文件/shell/network 访问拒绝、运行对象及持久来源身份改变、跨进程锁、真实 asyncio.gather 并发限制、无效参数后重试计数、计算/内存限制、代码大小与额外回调拒绝，以及忽略取消的底层请求保持配额直至结束。全量后端 248 项通过、12 个已有警告，相关 Black/isort 检查通过。

此原型尚未接入应用的独立 CodeMode MCP HTTP 端点、真实请求 Key、最后一次 SDK dispatch、关闭/热切换生命周期及 ordinary MCP 客户端矩阵。故 5.13–5.18 和 Gate Code 仍未完成，不能用这些核心单元测试证明完整端点可用，也没有构建或部署替换用户在用服务。上传 Skill 仍按文件分发，不提供按上传路径读/运行脚本的执行接口。

## CodeMode 独立 HTTP 端点接入

后续实施新增 `CodeModeRuntime`，默认关闭时不挂载端点、不创建身份盐或 worker。明确启用后挂载配置的 `/mcp/<path_name>`，使用官方 CodeMode 和受限 Monty bridge，仅公开 search/get_schema/execute。父应用管理公开 HTTP lifespan；当前配置关闭/改路径立即失效，管理员配置变更取消既有编排，停用后实际旧路由归属仍保留。CodeMode 不写入用户服务列表，原 Catalog HTTP/REST 保持原 search_tools/call_tool。

身份从当前请求的认证头重新验证 READ，持久私有随机盐为共享数据库 worker 产生相同 HMAC 配额身份。盐不进入旧配置导出、CLI bootstrap 或 MCP metadata。来源身份在明确配置/注册时创建；请求只读取当前持久身份及运行对象，不能复活退休来源。内部业务 get/dispatch 必须处于受限执行上下文，直接猜业务工具名称会在查询上游描述之前拒绝；SDK 最后一次分发再次校验当前身份、配置和来源。官方元工具 list/get 均保留网关组件标记，回代理到本实例时也不会递归收录。

AuthMiddleware 和 ASGI 层都明确将实际 CodeMode 路径视为需要 READ Key，不能借用同名但已关闭 Catalog 的 require_auth=false。现代 tools/call 测试客户端补齐与 name 对应的 Mcp-Name；不能把缺失现代路由头导致的拒绝误判成执行授权错误。官方 execute 对标量结果使用文本内容，业务工具结构化数据在 sandbox 中可正确计算，没有强行改旧 MCP 结果契约。

跨进程配额增加身份级短互斥并探测全部槽，降低允许并发后，较高编号的未确认取消槽也计入当前额度。配额文件访问错误不回显宿主路径，部分探测失败也释放已获得的临时锁。

`test_code_mode_gateway.py` 5 项使用真实 MCPServerManager、应用工厂/HTTP、官方 CodeMode 与 Monty：匿名拒绝、三个元工具及标记、搜索/原始 Schema/计算结果、业务直调拒绝、原 Catalog HTTP 并存、真实 Key 在业务调用中撤销后不再调度、关闭路径保护、已关闭公开 Catalog 同名路径仍强制 Key、管理员关闭取消慢执行并释放槽/重新启用。桥接测试新增配额降低时的高编号保留槽回归。全量后端 254 项通过、12 个已有警告；相关 Black/isort 与 diff 检查通过。

据此完成任务 5.13，但尚未执行完整 modern/legacy 普通 SDK 客户端、来源热切换/上游权限、多身份 HTTP 并发/限额/取消矩阵；5.14–5.18 和 Gate Code 保持待验收。也没有升级用户在用环境、重启或发布服务，不应把这一阶段视为可直接生产启用的最终验收。

## CodeMode 来源取消与原生客户端验证

来源停用先撤回目录资格，再取消已使用该来源的编排；尚未完成来源识别的目录查询按执行快照保守取消。重启和移除沿用相同停止流程。工具成功返回后再次核对来源代际，避免单次调用直接返回替换前结果。已完成调用仍记录为完成，不宣称其副作用回滚。管理操作若从当前工具回调内触发，只封闭自身执行作用域，避免等待持有回调的父任务而死锁；外部关闭继续取消并清理。未确认结束的底层任务仍保留配额直到实际结束。

新增原生 FastMCP Client/StreamableHttpTransport 测试，分别强制现代 `2026-07-28` 和 `legacy` 模式，验证公开元工具、实际计算和原 Catalog 并存。此处是 SDK 2.3 的 legacy 握手模式，不等同于已验收所有历史 SDK、mcporter 或第三方 Host。实际 HTTP 验证同 Key 并发限额、不同 Key 独立额度、客户端取消清理、重新获取执行槽与来源停止拒绝旧工具。配合既有真实 Monty CPU/内存/调用次数/并发/工具超时/总 deadline、宿主访问拒绝和安全审计测试，完成任务 5.15–5.17。

本阶段沙箱与网关 32 项通过，全量后端 261 项通过。完整跨上游权限矩阵及全部普通客户端安全矩阵仍待验证，5.14/5.18 与 Gate Code 不据此提前完成。未构建或替换用户运行服务。

## Apps 管理 API 与冻结版本

新增独立 `/api/apps` 管理接口：列表、含明确版本的详情、静态上传、草稿审核配置、确认发布、撤销、原始下载和文件源码预览。默认 `MCPCAT_APPS_ENABLED=false` 不因创建数据库表而启用；未登录拒绝，READ 不见草稿最新版本、不读取撤销版本，WRITE 可保留管理查看。内容和审核项在发布后冻结，再修改须上传新 SemVer。撤销新版本不会删除旧已发布版本或改变其原始下载字节；没有隐式 latest 下载。

使用固定 FastMCP 的 ResourceCSP/ResourcePermissions 字段，管理审核模型进一步禁止未知字段、通配来源、凭证/路径/非 HTTPS origin 和任意 sandbox 权限。权限请求只能使用已知官方空对象形式，是否授予仍由后续 Host 和用户决定。显式 `public` 选择作用于整个 App 的已发布版本，不是绕过远程端点鉴权的承诺。当前未经过运行来源校验的工具绑定拒绝发布，9.4 的动态绑定和逐调用授权尚未完成。

共享 `verified_artifacts.py` 为 Apps 和 Skill 变体提供根目录边界、大小与原始 SHA 校验，保留旧 Skill 异常/字节契约。Apps 额外重验包内完整清单。文件预览是私有 no-store JSON，不将上传 HTML 在管理页执行；原始下载强制 attachment/nosniff，包含完整 SHA。二进制或超过 512 KiB 的源码不给伪造文本，仍可下载原包。所有存储/校验置于线程池；发布、配置与撤销共用跨进程版本锁。

Apps 管理新增 13 项实际 HTTP 回归，覆盖权限、审核、冻结、旧包 SHA、草稿 latest 隔离、撤销回退、源码/Markdown/二进制/大文件、路径穿越、篡改清单/制品、未知 CSP/权限、无验证绑定发布拒绝与关闭开关。全量后端 274 项通过，旧 Skills/生成/协议/CodeMode 回归保持通过。任务 9.3 完成，但远程 Apps 端点、真实工具绑定/交互、隔离预览、前端及 Host 验收仍未完成，未部署用户服务。

## Apps 显式绑定与调用授权（实施中）

草稿新增 `PUT /api/apps/{slug}/versions/{version}/bindings`，完整替换最多 100 个明确绑定。别名仅在当前 App 版本内有效，不授权目录、不按 Catalog 同名工具回退。先验证完整集合，再以版本锁原子提交；失败保留旧绑定。发布通过公开 Provider 重新读取工具，审核摘要包含持久来源 UUID、原始 Schema 与 annotations，同名来源重建或 Schema 改动要求重新审核。异步验证结束后，锁内再次检查精确绑定集合、运行对象和当前 WRITE 权限；空绑定也不得跳过权限检查。

内部 `call_app_tool` 仅调用已发布版本的明确别名，使用真实 FastMCP Client/公开 call_tool_mcp，保留原 MCP content/structuredContent/metadata，不自行执行用户文件。工具目录有有限等待，Client 初始化后再次核对 Schema、发布/绑定与来源代际；当前 READ 身份由调用方的实时授权回调验证。调用返回后仍检查撤销和来源变化；若操作已经完成，不承诺回滚且不自动重试。安全审计不记录参数、结果、Key 或原始上游异常，取消单独记录。

新增 7 项测试覆盖跨服务同名工具、失败保留绑定、已发布冻结、猜测未授权别名、删除同名重建、Schema 变化、服务停用、真实 Key 撤销、版本撤销、调用中撤销后的单次副作用与安全摘要，以及 WRITE 降为 READ 时拒绝空绑定提交。审计测试暴露 Alembic 初始化默认禁用已有业务 logger，现使用 `disable_existing_loggers=False`，防止迁移使审计静默消失。

全量后端 281 项通过，相关 Black/isort 和 OpenSpec strict 校验通过。这仍不是完整 9.4/Gate D 验收：实际 Apps MCP 端点/Host、跨身份上游认证和完整取消矩阵尚未完成；内部调用授权适配不等同于已提供真实预览或 UI-only 回调入口。未升级或重启用户服务。

## 上传 Prefab 官方 renderer 与固定 UI URI（实施中）

新增 `prepare_prefab_app`：仅接受审核的 prefab-ui 0.20.2，使用公开 `PrefabApp.from_json/to_json` 处理已验证 JSON，以官方 `get_renderer_html("bundled")` 返回自包含界面。上传数据不在服务器求值，状态插值、动作 arguments 与成功回调保持客户端动态行为。只有审核绑定的别名可以映射到当前版本公开工具名，未知/额外/非法映射拒绝。派生 HTML 记录自身 SHA 和 renderer 版本，原始上传字节、清单与 SHA 不改变；官方 renderer 的必要 CSP 与明确审核域名合并，不引入通配符或放大权限。

补齐真实官方 wire 的 `$prefab.version` 校验，不能只验证简化上传示例的顶层 version。新增固定 `ui://mcpcat/<instance>/<endpoint>/<app-id>/<SemVer>/entry` 与普通附件 `app-file://mcpcat/<instance>/<endpoint>/<app-id>/<SemVer>/files/<path>`：派生入口和同名原始附件不冲突，UUID/编号/版本/路径均要求规范编码，拒绝 query、fragment、穿越、双义编码和 malformed UTF-8。附件不占用官方 UI scheme/MIME，避免 Host 将二进制等普通资源误判为 HTML 界面。该模块尚未作为运行端点公开，不把 URI 生成等同于资源服务已上线。

新增 25 项回归验证官方 renderer 实际字节/SHA、动态 state/动作、原包未变、CSP/权限、真实协议头、未知绑定、代码字符串不求值、renderer 漂移，以及固定版本、Unicode/构建号和恶意 URI。全量后端 306 项通过。HTML ZIP 附件装配、实际 UI 资源/回调端点、官方 Host 与预览仍待完成，任务 1.8、9.6、9.7 不提前完成。

## 上传 App 资源 Provider 原型（实施中）

使用公开 FastMCP Provider/Resource/ResourceContent 实现固定 URI 的 UI 与附件读取。Registry 在列表、直接查找、资源对象的实际读取、异步校验/装配之后重新核对当前授权、开关、持久端点身份和工具资格。仅已发布上传版本可读，旧 Resource 对象不成为授权快照，草稿、撤销、其他实例/端点和不存在附件拒绝。原始包读取/装配放在线程池；从清单取得的附件仍保持文本/二进制原始内容。

UI 入口使用官方 `text/html;profile=mcp-app` MIME 和 CSP/permissions；附件使用独立普通 `app-file://` MCP URI 与实际 MIME，而非 UI scheme。依据 [官方 Apps 规范](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/draft/apps.mdx) 与 [官方 UI Resource 概览](https://apps.extensions.modelcontextprotocol.io/api/documents/Overview.html)（2026-10-15 核对），UI resources 是 HTML 模板，其 MIME 必须为上述值；这并不把官方 UI 格式扩张为任意附件。

6 项原生 FastMCP Client 测试分别覆盖现代 2026-07-28/legacy 的 resources/list/read、官方 bundled renderer 实际字节和 UI metadata、Markdown/binary、旧对象权限失效、来源资格失效、撤销、跨实例/端点、草稿新版本和异步读取中撤销。全量后端 312 项通过，相关格式与 OpenSpec strict 通过。此原型尚未挂入生产应用，不等同于 HTTP Key、匿名公开审核或真实 Host/动态回调验收。HTML ZIP 入口装配、Apps 工具 Provider/运行端点和完整 Gate D 仍待完成，相关任务不提前打勾。

## Apps 打开与 UI 回调 Provider（实施中）

新增公开 Tool Provider，打开工具固定 App ID/version，关联同版本 UI URI，返回官方 Prefab structuredContent 和可供不支持 UI 的客户端阅读的说明。公开回调名称由 App ID/version/审核别名生成有界摘要，避免跨 App、跨版本或打开动作发生名称冲突；原始 JSON action 在装配时映射到这些真实工具名。打开不接受未声明参数或通过参数覆盖版本。

回调沿用源工具输入/输出 Schema、annotations 和未知 metadata，明确使用 `visibility=["app"]` 与网关 Apps 标记。受众标记不作为鉴权：直猜名称、保留旧 Tool 对象及普通 MCP 调用仍进入 Registry 和逐调用授权链，源服务停用、Key/授权失效、版本撤销或绑定变化均拒绝。调用结果保留 MCP content、structuredContent、metadata 与 isError，不在网关执行上传文件。打开/后端默认不会进入 CodeMode；Catalog 发现和真实 Apps 端点仍待接入。

5 项原生 Client 回归覆盖现代 2026-07-28/legacy 打开 metadata、动态 state、原始动作到公开回调名、真实业务调用/结构化结果/自定义 metadata、受众声明、授权撤销、源停止、撤销、旧对象和命名隔离。全量后端 317 项通过；Provider 尚未挂入生产应用，实际 HTTP Key/匿名审核/上游认证/Host/HTML 附件与 Gate D 保持未完成，不据此完成 9.4–9.8。

## 独立 Apps HTTP 端点与公开审核

新增 `AppsMCPRuntime` 和 `/api/admin/settings/apps-mcp`。`MCPCAT_APPS_ENABLED` 管理功能默认关闭，持久 apps.enabled 控制独立 `/mcp/apps` 远程入口且默认关闭；读取缺失配置不写默认值或创建端点身份。启用后 Provider 随父应用实际 lifespan 挂载，ready 来自运行状态。关闭或改路径立即停止新读取/调用；新路径须重启挂载，状态明确显示 restart_required。系统路径不写入普通用户服务列表或 Catalog。

启用检查普通配置服务、已加载服务、Catalog、独立 Skills 和 CodeMode 冲突；管理员保存和动态添加普通服务同时保护已挂载或待挂载 Apps 路径。关闭但未卸载的路由仍保留原归属，不能被其他端点覆盖。未知旧 apps 根配置保持存储且不在设置响应回显。

AuthMiddleware 与 ASGI 层分别检查当前真实 READ Key；MCP Provider 每次操作仍实时验证。require_auth=false 只放开传输入口，不公开 private App：匿名仅见明确 public 的已发布 App；错误非空 Key 不退回匿名。匿名绑定还要求来源自身明确公开，不能借公开 App 暴露私有 MCP 来源。所有 MCP HTTP 响应沿用 private/no-store 缓存隔离。与同名但关闭的公共 Catalog 配置共存时，也不能绕过 Apps 认证。

9 项真实应用 HTTP 测试覆盖 Key/公开审核、完整 6 MiB 官方 renderer 资源、structuredContent、私有缓存、真实 ready、撤销旧 URI、错误 Key、关闭/改路径、未知配置不回显，以及普通服务/Skills/CodeMode/Catalog 冲突与同名公共 Catalog 绕过拒绝。全量后端 326 项通过，格式/diff/OpenSpec strict 检查通过，任务 9.7 完成。

此阶段仅证明端点/URI/认证/路径与缓存接入，不等同于完成 HTML ZIP 装配、实际 HTTP 回调及跨身份上游 OAuth、完整取消、前端/真实 Host 或 Gate D。现有 ZIP/CLI、旧普通 MCP/Catalog 契约保持原样；未升级运行环境、重启或发布用户服务。

## Apps 实际 HTTP 回调与匿名来源隔离

在独立 Apps HTTP 端点增加真实 READ Key 的源工具回调验收：管理上传/绑定/发布后，通过现代 tools/list 取得 UI-only 回调名称，再用 tools/call 执行真实内存 FastMCP 工具并保留 structuredContent。绑定来源在 SQLite 明确配置，夹具命令不启动；调用链使用原生 FastMCP Client，而非直接调用 Python 函数。

匿名公开 App 的来源仍需独立授权：绑定私有 require_auth 来源时，匿名目录为空，直猜已知回调名也拒绝且源函数未执行；来源自身明确公开后，匿名调用才返回正确结果。真实 Key 撤销后新 HTTP 请求拒绝；若源工具在调用中撤销 Key，当前响应为错误并明确“部分操作可能已完成”，后续请求拒绝，已完成源函数只有一次调用，没有整段自动重试。

Apps HTTP 回归现为 13 项，全量后端 330 项通过。此证据补齐真实请求身份进入内部回调及匿名来源隔离，不代表已经完成四种实际外部上游、OAuth 多身份、完整取消/审计身份、HTML 附件或真实 Host。9.4/9.5/9.12 和 Gate D 保持未完成；未重启或发布用户服务。

## HTML ZIP 静态资产解析基础（实施中）

新增纯静态 `AppAssetResolver` 和 tinycss2 1.5.1 CSS 语法树装配，依赖同步 requirements/pyproject/uv.lock。解析器安装到隔离测试环境，未修改用户运行虚拟环境；离线 lock check 通过。解析只查校验后的包内字节，不下载外部资源、不读宿主路径、不执行 JS/Python/Node 或用户构建命令。

包内引用按所在 CSS 的目录解析，允许归一化后仍在包中的 `../images`，拒绝越过包边界、未知路径、反斜线、危险协议及未审核 HTTPS origin。URL 语法节点替换为实际资产内容，普通字符串/注释不按 URL 误改；包内 @import 展开并保留 media 条件，循环/深度及派生大小受限，预算在 base64 编码及输出拼接前检查。原始文件字节不改变。复杂 layer/supports import 尚未装配，当前明确拒绝而非静默丢弃条件。

14 项测试覆盖相对/根路径/编码、AST 嵌套结构、字符串不误改、media import、外部审核但不下载、穿越/协议、循环/语法与容量检查。全量后端 344 项通过，离线依赖锁/格式/diff/OpenSpec strict 通过。参考 [tinycss2 官方 API](https://doc.courtbouillon.org/tinycss2/stable/api_reference.html) 的解析/序列化接口，未自写正则 CSS 重写器。

另核对 [Apps 官方 CSP 规范](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/draft/apps.mdx)：默认 script-src/style-src 允许内联但不任意允许 script/font 的 data/blob 来源。因而不能仅将所有 ZIP 文件改为 data URL 就认定可在真实 Host 渲染，也不会为兼容而放宽 sandbox/CSP。该基础模块尚未接入发布入口，HTML/JS 模块与附件完整装配、桥接和真实浏览器 Host 验收仍待完成，9.6 与 Gate D 不提前完成。

## CSS 条件 import 装配补齐

CSS 装配进一步支持命名/匿名 layer、supports 和 media 的组合，保留点分层名与条件结构。依据 [W3C CSS Cascade 5 的 import 语法与条件规则](https://www.w3.org/TR/css-cascade-5/#at-import)，层本身也受 import 条件控制；派生结构将层放在条件组内，不让为假的 supports/media 提前激活该层。无效的层名、空条件、重复或逆序条件拒绝，不静默改成无条件 CSS。

后置 import 若原本违反规则顺序也拒绝装配，避免本来无效的浏览器规则经内联后意外生效；空 layer 声明仍可位于 import 前。CSS 回归现为 26 项，新增 12 项条件/顺序场景；全量后端 356 项通过，格式/diff/OpenSpec strict 通过。完整 HTML/JS 入口和实际 Host 渲染尚未完成，此处只补齐静态资产解析能力，不完成 9.6/Gate D，也不改变已冻结包或运行服务。

## HTML 入口及经典静态附件交付（实施中）

新增 HTMLParser 装配与统一 RenderedApp，单 HTML/ZIP HTML 格式进入独立 Apps 打开/Resources 链。内联 JS 不作字符串替换；包内经典脚本按原始 UTF-8 字节进行 base64 编码，由固定客户端 DOM 装载器设置 script.textContent，避免脚本中的 `</script>` 或 tagged-template 原文被改写/截断。包内 stylesheet 使用 CSS AST 装配再交付 style.textContent，图片/媒体从包内已验证字节取得。原 style 元素/属性也解析其 URL，HTML 实体、SVG 关闭标签保留；服务器没有执行 JS 或用户构建流程。

原资产声明 integrity 时在装配前检查 SHA-256/384/512；外部审核资源保留引用但不在服务器下载。HTML metadata 继续使用明确审核 CSP/权限，不为装载器扩大域名或 sandbox。工具结果返回公开、固定版本的 toolBindings 映射供作者接入官方 Apps SDK，不含 Key。原 ZIP/SHA/文件清单保持不变，派生模型包含独立摘要及装配版本；摘要持久化/漂移验收仍待补齐。

新增 12 项 HTML 单元回归及 1 项实际 HTTP ZIP 上传/发布/打开/UI 资源/原下载回归；与现有资源/工具链相关 37 项通过，全量后端 369 项通过。覆盖 JS 原字节、中文/结束标签、CSS/图片、实体/SVG、integrity、外部审核、重复属性、包外引用和显式未支持参数。

当前仅完成经典静态路径：模块分块、defer/async、srcset、嵌套框架、完整别名 SDK 示例及真实浏览器时序仍待实现/验收，未支持项明确拒绝，不能据此把 HTML ZIP 全能力或 9.6/Gate D 打勾。尚未构建前端、升级环境或重启用户服务。

## HTML 经典装载器真实浏览器验证

新增可复验夹具 `tests/browser/app_html_fixture.py`，仅监听 127.0.0.1 临时端口，不接入用户数据库或 8000 服务。IAB 真实浏览器在 `script-src 'self' 'unsafe-inline'`、`style-src 'self' 'unsafe-inline'`、`connect-src 'none'` 的限制性 CSP 下验证经典装载器，而不是 Python 字符串断言：首次页显示 `Client script executed`；raw 页实际显示 `中文 </script> 😀`；ordered 页实际显示 `first,second,third; rgb(0, 128, 0); third-script`。证明此路径的客户端执行、UTF-8/tagged-template 原文、同步次序、样式与 currentScript 属性可用。

这不是官方 Apps Host 或隔离 bridge 验收：页面为自有本机浏览器夹具，未涉及用户凭证，不能扩大为 module/defer/async、完整初始化/事件、iframe 会话或 Gate D 完成。验收后已关闭临时服务及标签页，没有修改或重启现有服务。

同时修正静态装配 integrity 多摘要选择，按 [W3C SRI 强算法优先规则](https://www.w3.org/TR/sri/#get-the-strongest-metadata) 验证最强受支持算法，较弱正确摘要不能掩盖较强不匹配。算法大小写与非 ASCII 非法摘要安全处理；单元回归增加强/弱组合用例。全量后端 370 项通过，格式/diff/OpenSpec strict 通过。

## 发布渲染预检与冻结摘要

App 发布在版本锁内使用与资源读取相同的完整装配流程，未完成的模块/defer/async 或缺失/非法资产不再先发布再在客户端失败，而是明确拒绝并保留草稿。装配不执行上传脚本。较慢渲染预检后、提交前再次核对绑定/当前授权与来源代际，防止预检期间失效仍提交发布。

新增 Alembic `0005_app_render_review`，只给 app_versions 增加可空 `rendered_sha256`、`rendering_version` 与 `rendered_payload_sha256`，不改写原始包、已有发布状态或旧 Skills。发布记录 HTML 字节、装配版本以及 structuredContent/工具名映射/CSP/permissions 的规范 JSON 摘要；读取重新装配时核对，发现漂移要求审核新版本，不偷偷将同一 URI 替换为新输出。历史空摘要保留原数据与原下载，不通过迁移自动重新发布。

新增 3 项回归覆盖发布摘要落库、HTML/派生数据漂移拒绝、未完成装配保留草稿，以及真实 revision 0004 增量升级后旧 Apps 状态/说明不变。全量后端 373 项通过，格式/diff/OpenSpec strict 通过。原始下载继续只按原包摘要返回原字节。完整模块/Host 验收仍待完成，9.6 与 Gate D 不据此完成；未升级或重启用户实例。

## Catalog 的 Apps 受众边界

Catalog 的模型搜索/调用入口复用固定 FastMCP 的公开 `is_model_visible` 判断，不再把 `visibility=["app"]` 或空受众工具送入普通搜索，也不允许从 Search 的 call_tool 包装入口直猜这些名称。原来源 MCP 仍按其自身授权提供 UI 回调，过滤不是全局删除、更不把 visibility 当鉴权。

模型可见的 App 打开动作继续保留原 UI metadata，普通工具契约不变。命名冲突先对完整原始目录检查，再过滤受众，防止隐藏一侧意外启用另一来源的同名公开别名。新增 3 项回归覆盖目录、直猜拒绝、源 MCP 仍可调用、隐藏侧冲突、实际 Search Transform 的搜索/调用与普通工具；相关 Catalog/CodeMode 20 项通过。

全量后端 376 项通过，格式/diff/OpenSpec strict 通过。Catalog App 的完整资源/回调路由和源身份隔离尚未完成，保留打开 metadata 不等于可渲染，因此 9.8/Gate D 不提前完成。未构建或重启用户服务。

## CodeMode 前端管理员设置

全局设置增加独立 CodeMode 管理卡片，仅 WRITE 管理员挂载/读取；状态来自 `/api/admin/settings/code-mode` 的真实 ready/restart_required，不把保存成功视为已经就绪。提供默认关闭开关、合法路径、八项有限正数限制，以及服务/工具范围三个明确状态：null 不额外限制原 Catalog、空数组禁止全部、名单只进一步缩小范围。固定 require_auth=true 不给匿名开关，不显示实际 Key。

保存期间锁住输入与按钮；失败保留编辑并允许重试，加载失败不以缺省配置覆盖服务器。显示服务端执行位置、上传 Skill 不执行、部分副作用不保证撤销及不自动重试说明。只有真实 ready 时显示接入路径，另提供由 MCP 客户端负责握手/协议头的无凭证 search 消息示例，没有执行工作台，不改原 Catalog/已发布 Skill 绑定。

Flutter 规范影响本实现：网络/可替换 loader/saver 放在 Riverpod Provider，本地表单状态与校验留在 Widget，复用项目 Material 3/AppCard 主题与响应式 Wrap。5 项新 Widget 测试覆盖 360px、有限额度/null 范围、pending 不误报 ready、慢请求锁/失败恢复、NaN 拒绝、加载重试和禁止全部确实提交空数组。全量 Flutter 51 项通过，四个新增/相关文件 analyze 无问题，结合已有后端默认关闭/非法配置/原 Catalog 并存测试，完成 7.8。

代码尚未构建同步到后端静态目录或重启服务；实际 Web/Host/发布 Gate 保持未完成。没有更新 Flutter SDK 或依赖版本。

## 前端固定变体浏览

管理列表与详情改用独立 `/api/skill-variants`，包含 remote-only，保留服务/Catalog 来源摘要。新管理摘要的已发布版本同时考虑 local/remote；READ 用户的 latest 不泄露新草稿版本号。旧 Registry v1 与 CLI 默认 local 没有变化。

详情按固定版本、profile、稳定 binding 选择文件和下载 ZIP。不向 remote-only 伪造本地安装入口；具备 local 的版本仍保留 CLI 指引。文件浏览器继续支持 Markdown 渲染/源码、文本、二进制与大小限制提示，版本或模式/绑定变化重新加载，并隔离未完成的旧请求。独立端点和来源端点的身份、工具依赖明确展示，上传脚本执行位置仍为客户本地。

新增模型、API URI/查询参数、360px remote-only 浏览下载、同路径切换、慢请求和无 key 版本切换回归。本阶段全量 Flutter 29 项通过，11 个相关源码/测试文件分析无问题；全量后端 201 项通过、12 个已有警告。代码尚未构建同步到后端 Web 目录，也未升级本机在用数据库/重启服务。管理员设置和 Apps/CodeMode 页面仍待实现，不把本次浏览能力等同于完整前端验收。

## 远程接入面板与连续反馈

固定 remote 版本/绑定从 connections API 查询实际状态，提供加载、失败/重试、不可用/草稿说明、无凭证地址复制和客户端扩展支持提示。不能借用其他版本或绑定的地址，旧慢请求不能覆盖新选择。当前认证头只展示 READ Key 占位符；私有内容即使端点关闭认证，仍要求 Key。新管理接口增加明确公开审核标记，协议匿名读取实际测试与 UI 说明一致。拒绝复制含用户名/密码、查询参数或非 HTTP(S) 的异常 URL。

旧 local-only 保留原安装入口，并可选择远程模式生成新版本。历史上传的冻结版本经明确独立端点适用性确认后，共享原字节为更高版本双模式草稿；不修改原包、不执行脚本。生成完成选中实际返回版本；三个生成入口统一幂等反馈，unchanged 不误报新草稿。修正详情/接入重试的 setState 回调返回 Future 问题。失败后释放来源锁，重试时所有来源入口仍受锁保护。

全量 Flutter 42 项通过（含固定连接身份、360px、复制占位符、失败恢复、Tab/Enter 模式选择和 Enter 源码切换），15 个相关源码/测试文件分析无问题。服务卡片已有 withOpacity/async context 提示未扩展为全文件重构。全量后端 201 项通过、12 个已有警告；独立/来源 MCP 与管理内容相关 35 项通过，其中新增断言确认端点免认证不等同于私有内容公开。任务 7.2/7.4/7.5 按这些证据完成；实际 Web 部署、真实 Host、Apps、CodeMode 和恢复验收仍未完成。

## 普通上传双模式与四入口回归

上传对话框默认 local 走旧接口，remote/both 走独立管理上传。使用不可变文件快照、可替换选择/上传 Provider，以及服务端返回的当前运行服务/Catalog/独立端点选项。未知类型不猜测，失效绑定在提交前及服务端再次拒绝。远程提交必须确认端点适用性和客户本地执行前提；选择变化重新确认。两种方式共用上传文件，不自动重写调用指令、不执行脚本。慢上传禁止重复操作，成功后锁定参数，失败保留文件以供重试。

新的 multipart 上传保留变更说明，返回验证后的 SHA/文件/脚本清单；打包/存储和制品读取置于线程池，原 v1 响应不变。绑定查询不创建运行身份、不返回服务配置或密钥。

全量后端 205 项通过、12 个已有警告；全量 Flutter 46 项通过，6 个相关源码/测试文件分析无问题。新增应用工厂链路通过实际 HTTP 管理接口上传含 Python 脚本的 both 包，预览两模式、审核发布、旧 local ZIP 下载与新 remote ZIP SHA/字节一致，再通过官方 MCP Resources 取得同一脚本字节；独立端点 tools/list 为空，脚本落盘标记不存在。另有绑定停止阻断、选项排除停止服务/不泄密、multipart 模式/绑定/长超时、窄屏审核、慢上传和失败重试回归。结合既有生成与协议测试，任务 7.7 完成；这不是第三方 Host 自动激活、实际 Web 部署或完整发布 Gate 的验收结论。
