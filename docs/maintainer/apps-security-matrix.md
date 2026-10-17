# Apps 安全与兼容矩阵（9.12）

本页记录网关与管理预览的安全回归，不是第三方 Host 认证。FastMCP 4.1.0、MCP Python SDK 2.3.0、Prefab 0.20.2；原生 Client 的现代模式为 2026-07-28，legacy 为 2025-11-25。其他旧协议/传输的基础代理证据见升级记录 Gate A，不能从这两个 Client 模式外推全部 Host 支持。

## 逐项证据

| 要求 | 当前证据 | 范围与边界 |
| --- | --- | --- |
| 同名多来源不串源 | `test_upstream_apps_proxy.py`、`test_catalog_upstream_apps.py`、`test_catalog_app_identity.py` | 原工具/资源名相同，动态结果、二进制、来源身份与回调分别核对；Catalog HTTP 原生 Client 与工厂代理均覆盖 |
| 普通工具/App 别名冲突 | `test_app_classification_does_not_revive_ambiguous_catalog_names` | 普通工具 `alpha/beta__open_html` 与 App `alpha__beta/open_html` 的同名别名均拒绝；包括以 `catapp_` 开头的服务名和缩小 CodeMode 范围，不能过滤 App 后复活普通别名 |
| Prefab 动态数据/动作 | `test_app_prefab_renderer.py`、`test_catalog_prefab_dynamic_payload_stays_unmodified`、`test_app_tools_provider.py` | 原始 state/structuredContent/动作不被字符串替换；授权交互改变来源数据；真实第三方 Host 展示仍属 9.13 |
| HTML/附件 | `test_app_module_graph.py`、`test_app_module_v5.py`、`test_app_css_assets.py`、`test_app_srcset_assets.py` 及装配验收浏览器证据 | 相对文件、模块/JSON/动态 import、作者 map、附件、MIME/字节与冻结版本；见 [装配验收](html-module-assembly.md) |
| 交互写入/明确绑定 | `test_app_tool_bindings.py`、`test_apps_mcp_gateway.py`、`test_app_preview_call.py` | 未绑定拒绝，写入/未知副作用的管理预览需显式确认；MCP Host 负责自身授权交互，网关仍逐次检查 |
| 执行中撤权/撤销/停用 | `test_apps_inflight_authorization.py` 12 项 | Apps/Catalog × 现代/legacy × Key 撤销、App 撤销、源停用；已开始的效果只发生一次，结果拒绝并提示部分操作可能已完成，不自动重试/虚构回滚 |
| 上游权限与凭证 | `test_app_proxy_authorization.py`、Catalog 工厂代理认证用例 | 静态头/OAuth 使用来源配置；403 后不执行第二次业务调用，mcpcat Key 不进入上游请求、UI 或错误正文 |
| 草稿/私有/旧 URI | `test_app_resources_provider.py`、`test_apps_mcp_gateway.py`、`test_catalog_uploaded_apps.py` | WRITE 不使草稿经 MCP 可读；匿名需 App 与来源分别公开；撤销、Key 失效和来源变化后拒绝旧对象 |
| 路径/容量/不可变审核 | `test_app_registry.py`、`test_app_render_review.py`、`test_app_review_policy.py`、`test_app_resource_uri.py` | 拒绝穿越、非法包、超限、未审核外域和被改写的派生内容；服务器不执行上传后端或用户构建流程 |
| CSP/窗口与会话 | 上述审核测试、`tests/catalog_app_relay.test.mjs`、来源中继浏览器夹具、前端 Host 测试 | 默认隔离、来源 Window/origin、旧会话、保留 sandbox 消息、大小/请求数上限、原 frame-src 恢复；不宣称所有 Host 接受嵌套 iframe |
| 旧 Host/普通结果 | `test_catalog_uploaded_apps.py`、`test_catalog_upstream_apps.py`、`test_catalog_apps_visibility.py` | 原 synthetic 工具继续存在，UI-only 不进入搜索/CodeMode；打开说明指出 Apps Host 前提，原上游普通结果保留，不强制渲染 |
| 断连与停止 | `test_catalog_apps_network.py` 10 项、`test_app_mcp_cancellation.py` 3 项 | 真实 TCP 的现代/legacy 工具与资源取消、上传 App 的 Apps/Catalog 取消、取消后会话继续可用、管理器停止及标记清空；不合作收尾有界等待并保留跟踪 |

## 取消发现的实际缺陷与修复

固定 SDK 的 JSON 响应模式可承载数 MiB 的 Prefab renderer，但现代请求断开不会自行监听取消。原 TCP 用例明确失败：客户端已取消，上游异步慢工具仍等待。`AppDisconnectMiddleware` 在完成 MCP 请求解析之后监听当前 HTTP 断开，复用原预览的有界取消/收尾；没有修改 SDK 私有 dispatcher、重复读取请求正文或改掉大资源传输。

补充逻辑只作用于现代 2026-07-28 请求。旧版继续使用 SDK 原生 `notifications/cancelled`，无 HTTP 上下文的内存客户端继续直接运行。取消后测试再执行实际目录/工具请求，确认会话仍可用；不会仅以客户任务已取消作为上游已停止的证明。

Catalog 是服务器内直调来源公开 API，不能只登记 `/mcp/{source}` 的 ASGI 请求。`source_operation` 将实际来源调用任务加入现有管理器生命周期，来源停止/重启可取消它并等待清理。停止后清除活动集合/停止标记，不把已经开始的副作用视为事务回滚。异步工具不合作或外部系统已经提交时，仍不能保证终止或撤销实际业务效果。

## 本阶段未包含的验收与后续结果

本轮完整后端 **768 passed，70 warnings**（包含需要临时回环端口的测试），Node 来源中继 **11 passed**。未隐藏现有 Pydantic/tree-sitter 等警告；此前真实 TCP 现代取消用例的失败已由修复后的同一矩阵覆盖，不以跳过失败或缩小断言代替。9.12 完成，9.13 仍未完成。

后续 9.13 已完成，见 [官方 SDK 参考 Host 与管理预览实际交互](apps-host-verification.md) 和 [最终证据索引](upgrade-evidence-index.md)。上述 768 项及“仍未完成”是当时阶段结果，最终完整后端现为 785 项通过。固定 SDK 参考 Host 证据不扩大为所有第三方商业 Host 支持。

- 独立真实 Apps Host 的渲染、用户点击、权限提示与该 Host 的 CSP 实际行为，以及前端构建到后端：9.13 单独验证。
- 原生客户端/浏览器协议夹具不证明 Agent 自动激活 Skills/Apps；不把固定 Prefab 数据解析通过称为完成真实 Host 展示。
- 没有重启用户服务、写入真实数据库/凭证、执行用户 MCP、推送或发布 npm/Docker；全部主动调用来自隔离测试实例与可信夹具。
