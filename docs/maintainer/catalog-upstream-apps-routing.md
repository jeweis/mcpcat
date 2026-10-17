# Catalog 上游 Apps 的来源绑定与验收

9.8 已接入正式 Catalog Provider：显式启用 Apps 后，可发现/打开上游 App、读取来源隔离的 UI/附件、调用隐藏 UI 回调，CodeMode 排除 App 与 UI-only 后端。现代/legacy Client、实际代理认证链和浏览器中继夹具均有证据。这不等于所有第三方 Host 已可用，也不代替 9.12/9.13 的完整安全/真实 Host 验收。

## 固定事实与设计选择

现有 FastMCP 4.1.0 Namespace 不改写 ui.resourceUri 或自定义 HTML 内部的回调名。固定 @modelcontextprotocol/ext-apps 2.0.3 的 AppBridge 直接将 App 的 tools/call 参数交给 MCP Client，不自动添加可用于聚合来源路由的 resourceUri。仅给工具名加前缀、按“最后打开”路由或复用重复 App hash 都不足以隔离来源。

[官方 Apps 规范](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx) 将 UI 与 Host 的通信定义为 postMessage 上的 JSON-RPC，并明确保留 tools/call、resources/read；sandbox 控制通知不能透传。官方允许不用 SDK 直接实现这层协议，[概览说明](https://modelcontextprotocol.io/extensions/apps/overview)也指出 App 类只是便利封装。因此本次适配的是来源消息边界，不重写上游 JS/HTML，也不实现第二套 Prefab renderer。

已实现固定的来源适配壳，将原始 HTML 字节编码为数据并放入独立 iframe，中继标准消息。外层仅为固定壳保留必要的 about: 嵌套框架能力，内层附加 CSP 恢复原来源 frame-src 限制；其余 CSP 从 Host 继承，不手工扩大 connect/resource domains。本机浏览器夹具已验证，但不能据此认定所有真实 MCP Apps Host 支持此嵌套能力。

## 已实现的纯核心

`catalog_app_identity.py`：持久来源 UUID + 当前运行代际 UUID；资源 URI 包裹原 URI 的规范 Base64URL，原 URI 的 Unicode、query、fragment、未知合法 scheme 不变。回调名是两个身份和原工具名的摘要，长度 55 字符，不占用用户可变服务名称，也不因同名服务重建而沿用旧名称。

标识不是授权凭证。Provider 每次校验当前 READ Key、来源是否公开/纳入、当前持久身份、运行对象/代际，并通过源服务器公开调用链保留上游认证；不能因为客户端知道 URI 或回调名就视为有权调用。请求不创建已退休的来源身份。

`catalog_app_relay_core.js`：

- 原工具名只映射到当前来源明确提供的 callback；未知名和其他来源 callback 拒绝，不兜底到 Catalog synthetic call_tool。
- resources/read 的 URI 由同一来源/代际包裹，响应恢复原 URI；文本/blob/MIME/未知 metadata 不因此降级。
- UI 请求 ID 加独立会话 UUID，返回时恢复；旧 View 的响应不匹配新 View，相同本地 ID 不串会话。
- cancellation 只映射当前 View 的未完成请求，关闭后拒绝新请求，并返回待取消的 wire ID 给后续浏览器壳清理。
- 保留标准 ui/* 方法、ping、日志、工具及资源读取；拒绝未支持的 MCP 方法、通知式工具执行和保留的 sandbox 控制消息。
- JSON 消息上限 16 MiB、未完成请求上限 256，重复 ID 拒绝；不记录调用参数、结果或任何 Key。

这里的 JavaScript 仅为客户浏览器的固定协议适配器；不在服务器运行上游 UI 或上传脚本。

## 浏览器壳与实时来源快照

`catalog_app_relay.py` 保留原 HTML 字节与 SHA，原文只作为 Base64 JSON 数据进入固定壳，不拼入壳的执行脚本。已验证危险工具名不能结束外层 script。未知 metadata、连接/资源域名策略保持，派生壳与原文分别标识。

`catalog_app_relay_boot.js` 只接收精确 parent Window，首次有效消息后固定其 origin；内层只接受精确 iframe Window 和 opaque origin。Host 也处于 opaque iframe 时，`"null"` 不是合法的 postMessage targetOrigin，因此向精确 parent Window 发送时使用 `*`，接收侧的 Window/origin 校验仍保留。初始化前通知有限排队，关闭移除监听器/内层 iframe 并取消当前 View 的未完成请求；非法过大响应只失败其关联请求并释放槽位。

`catalog_app_sources.py` 只读取已存在的来源 UUID，不因请求创建或复活退休身份。每次检查来源开启、运行、Catalog 纳入、OAuth 排除和实时授权；异步操作前后用 `current()` 拒绝旧快照。运行对象替换产生新代际，同名来源重新登记后的 UUID 也不同。来源 UUID 在显式初始化/新增服务时登记，与资源请求分开。

## 正式 Catalog 接线

`CatalogUpstreamAppsProvider` 在原 Search Transform 外提供模型可见打开动作和资源；原搜索目录不再重复暴露带原始 UI URI 的打开动作。回调为来源/代际固定的 opaque 名称，只允许直接经当前来源调用，不进入模型工具目录或 synthetic `call_tool`。原 Apps 默认关闭和原 Catalog 流式响应保留；显式启用 Apps 时使用 JSON 响应承载较大的 Prefab renderer。

`catalog_app_request.py` 从当前 MCP HTTP 请求读取 Key，REST 边界通过 ContextVar 显式传递后恢复；不读取工具参数或客户端 metadata 的身份声明。公开 Catalog 只向匿名访问者列出公开来源，错误/撤销 Key 不回退匿名。源工具调用保留原参数/输出 Schema、注解、图标和当前版本，执行前重新查工具并核对 Schema；调用后来源变化报告结果不确定，不自动重试可能有副作用的操作。

原始 Prefab structuredContent/state/动作数据不改写。固定框架 `fastmcp.tool_hash` 只用于来源内验证原名/hash 别名，不留在 Catalog 全局 hash 分发元数据中，避免重复 App hash 绕过来源路由；其他允许 metadata 保留。同一来源的 hash 别名若歧义也拒绝。非 UI 资源保留原字节、MIME 和未知 metadata，UI 原文通过固定壳派生。

## 证据与未完成项

`test_catalog_app_identity.py` 15 项验证原 URI 字节、来源/代际隔离、回调名、非规范编码/额外路径/参数、非法及超长 URI。`test_catalog_app_sources.py` 9 项验证读取不创建身份、权限/生命周期变化和同名重建；`test_catalog_app_relay.py` 8 项验证原文/摘要、CSP 和执行脚本边界。Node 的 `tests/catalog_app_relay.test.mjs` 11 项验证同名回调分离、跨来源拒绝、保留 metadata、资源往返、sandbox 消息隔离、并发 ID 上限、取消、关闭、跨 View 旧响应拒绝和非法/过大消息。它们不是实际 MCP 授权或真实 Host 的证明。

本机浏览器夹具：`tests/browser/catalog_app_relay_fixture.py --port 18764`（需 `PYTHONPATH=.`）。`/` 与 `/opaque` 分别验证普通 Host 和 Host 自身也在 opaque iframe 的情况；两个同名 App 均显示 `checks=true; mapped=4; foreign=2; cancelled=2; teardown=2`。证明源窗口拒绝、同名路由、过期响应隔离、原资源 URI/blob/MIME/metadata 与关闭取消；它是无凭证、无真实业务工具、禁止外网的协议夹具，不冒充独立商业 Host 或官方 SDK Host。

`test_catalog_upstream_apps.py` 9 项覆盖现代/legacy Catalog HTTP 双同名来源、UI/二进制附件/未知 metadata、隐藏回调及 CodeMode 排除、Prefab 动态 state、撤权/停止/替换后的旧 Tool/URI 拒绝、匿名私有来源拒绝，以及真实工厂代理使用预配置上游认证且不转发 mcpcat Key。代理通过固定 ASGI HTTP 承载，不宣称它等价于全部 TCP/SSE/STDIO Host 矩阵。`test_catalog_app_tools.py` 2 项覆盖命名空间 hash 核对、冲突拒绝和全局 hash 路由隔离。

完整后端 **741 passed，70 warnings**（含需临时回环端口的测试）；随后来源/工具/壳定向回归 28 项通过，Node 11 项通过。新增 Python 通过 Black/isort/Flake8（与既有 Black 风格兼容的忽略项）检查。受限沙箱初跑的 4 项回环端口权限失败已在获准环境完整重跑通过，没有把环境失败隐藏为测试跳过。没有修改运行数据库、用户 MCP 配置/进程、前端构建或发布制品。

后续 9.12 的真实 TCP 取消/停止、普通/App 全局别名冲突和执行中撤权已补齐，完整后端 768 项通过，详见 [Apps 安全矩阵](apps-security-matrix.md)。9.8/9.12 完成不代替真实 Apps Host 自动渲染/激活；嵌套 iframe 的具体 Host CSP/能力与 UI 实际交互、完整恢复/构建仍由 9.13/Gate D 验收。更多任意框架重命名组合不由单个 hash 单元测试承诺。
