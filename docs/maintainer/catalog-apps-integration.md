# Catalog 与已托管 Apps 集成

2026-10-15，升级实施记录。只证明已托管上传 App 的 Catalog 接入；上游同名 App 的完整聚合隔离仍未完成，提案 9.8 不据此勾选。

## 实现边界

`CatalogAppsProvider` 使用当前 Apps runtime 的 `AppToolsProvider`、`AppResourcesProvider`、Registry 和 Resolver。同一 App ID/version 的 UI、附件和 callback 名称可经 `/mcp/apps` 或 Catalog 读取/调用，不复制内容、数据库、审核状态或凭证。

普通工具目录通过公开 `Provider.wrap_transform` 应用原 Search Transform，Apps Provider 放在同一 Catalog root 的另一分支。原 `search_tools`、`call_tool` 名称和普通调用契约不变；MCP 模型目录还会列出可打开的已发布 App，保留实际 UI metadata。

UI-only 回调不进入普通目录、搜索结果或 synthetic call_tool。Host 的直接 MCP 回调仍可调用，但每次验证当前身份、Apps 开关、固定版本发布、来源 UUID/状态/Schema 与原绑定。visibility 不是鉴权，也不能用“未显示”代替逐次授权。CodeMode 继续使用原始服务目录，不纳入这个 Apps Provider。

Catalog 公开不使私有 App 公开；匿名仍受 App 公开审核与来源公开性限制。关闭 Apps、停止来源或撤销 Key 后，已经打开的 UI 不能延长后续读取/调用权限。

## HTTP 与旧客户端

bundled Prefab renderer 约 6.6 MB，而固定旧客户端的单个 SSE 事件默认上限为 1 MB。明确启用 Apps 时，Catalog HTTP 使用框架公开 `json_response=True`，让现代与 legacy HTTP 客户端完整读取 renderer；不截断资源、换 CDN 或改写已冻结的包。

未启用 Apps 的旧部署保持原 HTTP 流式选择，legacy 客户端实际 tools/list 响应仍为 text/event-stream；现代列表按框架语义可直接为 JSON。Catalog 原 SSE 地址和普通 MCP 服务流式代理不变。需要 UI/大资源的客户端使用 HTTP；不能把 HTTP 的大 renderer 测试描述成所有 SSE 客户端均能承载大帧。

## 验证证据

`tests/test_catalog_uploaded_apps.py` 八项使用真实应用工厂、Catalog mount/lifespan、HTTP 协议序列化和实际 READ Key：

- 普通 synthetic 工具与 App 打开动作并存。
- Catalog 与独立 Apps 返回相同 HTML/CSP/metadata，原动态 payload 仍使用对应 callback 名称。
- modern/legacy 原生 Client 均能打开 App、读取完整 renderer、调用原绑定工具；旧客户端仍收到非交互说明文本。
- UI-only 猜名不能走 synthetic call_tool，不进入普通搜索或 CodeMode。
- 来源停止、Apps 关闭、Key 撤销后，旧资源和回调拒绝且不执行工具。
- 公共 Catalog 不泄露私有 App；未启用 Apps 时旧流式响应保留。

`tests/test_fastmcp_public_api_boundaries.py` 三项核对固定框架接口：Provider wrapper 不改原目录且正确管理 lifespan、扩展实例/clone 的绑定与启动后注册约束、Namespace 不会自动修复 ui.resourceUri。后者明确揭示上游 Catalog 适配的剩余工作，不是将已知缺口当成功验收。

本轮全量后端 653 项通过（27 个警告）。本证据为内存 ASGI 的真实 MCP 序列化，不冒充真实第三方 Host 点击渲染或真实网络全矩阵。没有改动运行数据库、用户 MCP 进程、部署静态文件或发布制品。

## 仍待完成

通用 Namespace 只改组件名/资源 URI，不同步改 ui.resourceUri，也不为自定义 HTML 的回调补充来源上下文。FastMCPApp 对重复 App hash 明确拒绝歧义，不应按最后匹配来源兜底。上游 Apps 的 Catalog 分发需要稳定来源资源/回调适配；禁止任意替换 HTML 字符串或按“最后打开的 App”路由。

完整 9.8、HTML 模块依赖装配、同名多来源矩阵和真实 Host/部署验收均继续待办。

后续进度：HTML/Prefab 装配已由 9.6 独立验收完成；上游来源 UUID/运行代际 URI 和消息中继核心见 [来源绑定实施记录](catalog-upstream-apps-routing.md)。该核心尚未挂载 Provider 或浏览器壳，9.8 仍未完成，不能把纯编解码/消息测试描述成真实上游 App 已可聚合打开。
