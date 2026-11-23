# 会话级上游连接复用（P2 设计）

## 目标
同一“下游会话”内复用一条已建立的上游连接，消除“每次操作新起进程”，
默认关闭、按能力门控、压测后再灰度。

## 关键约束：回调是“按请求绑定”的
FastMCPProxy 每个操作调用一次 `client_factory()`。当前在 `proxy_clients` 建 client 时，
用 `get_context()` 捕获**当前请求**的 sampling / roots / elicitation 回调；
`proxy_logging.request_log_handler()` 捕获 `session + origin_request_id`；
还有 progress / message handler。直接复用 client 会把通知串到旧请求。

**注意**：不能用 ContextVar 动态取“当前请求”——上游日志/进度回调在 SDK 的接收任务里触发，
不在请求任务里，ContextVar 不跨任务可见。

## 方案
1. 池键 = `(server_name, 下游 session_id)`（`get_context().session_id` 可用，已在 proxy_logging 使用）。
2. 复用 `SessionPool`（`app/services/session_pool.py`，已完成）：独占 checkout、上限、空闲 TTL、错误即弃。
3. **回调动态派发**：池化连接对象上保存 `current`（当前操作的 handlers，普通属性，跨任务可见）。
   回调 stub 在触发时读 `current` 转发。因为同一连接**独占**（SessionPool 保证一次只服务一个请求），
   任意时刻只有一个 `current`，不存在并发歧义。
4. 建连时把 client 的 sampling/roots/elicitation/log/progress/message handler 都构造成
   “委托到 conn.current”的 stub，而不是捕获创建时上下文。
5. 能力/安全门控（任一命中则不池化，走每请求新建）：
   - 下游声明 sampling / roots / elicitation；
   - `connection_mode=isolated`；
   - `require_auth:false` 的公共端点；
   - 非 stdio 且无收益的传输（HTTP 仅几十 ms，可不池化）。

## 配置
`connection_mode: auto(默认) | pooled | isolated`。默认 auto：满足门控则池化，否则新建。
P2 灰度期默认对纯工具型、无反向回调的服务开启；可用 `isolated` 显式关闭。

## 生命周期
- 会话结束 / 空闲超时 / 连接异常 → 关闭该连接；异常连接不入池。
- 每个连接常驻一个上游进程，受 `max_per_key` 与数据卷内存约束；需监控进程数与内存。

## 分阶段
- 3.1（本步）：设计 + `SessionPool` 基础（已完成，含单测）。
- 3.2：接线——pooled 模式构造持久 client + 动态 handler 派发；并发/隔离/回调路由/内存压测。
- 3.3：灰度开关、文档、监控。

## 风险
- 同一会话内保留上游进程状态（缓存/登录态）——按会话隔离可接受；不同会话绝不共享。
- 回调相关 request_id 路由需动态派发验证，否则 log/progress 可能错配。
- 连接在两次操作间失效需丢弃重连；错误即弃已覆盖。
