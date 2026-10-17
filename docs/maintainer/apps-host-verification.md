# Apps 真实参考 Host 与管理预览验收

2026-10-15，任务 9.13 已完成固定 SDK 参考 Host、实际管理预览、构建及完整恢复范围的验收。这里的 Host 是在真实浏览器运行的官方 `@modelcontextprotocol/ext-apps@2.0.3` App/AppBridge 参考宿主，不是 Claude/Codex 等第三方产品认证；其他 Host 仍须按确切版本独立验证。

## 实际链路

- 测试页面独立于 Flutter。打开、资源读取和回调都由 Python SDK 2.3.0 的实际 TCP MCP 客户端访问正式 `/mcp/apps` 或 `/mcp/mcpcat`，使用合成 READ Key，不调用管理 `preview-call` 来冒充 MCP 远程交互。
- 官方 App 客户端和 Host bridge 均从前端锁定依赖编译，不修改官方 SDK。界面运行于 opaque sandbox，父页显示当前调用及参数，逐次批准；服务器仍进行正式 MCP 授权和来源检查。
- 上传夹具由正式上传/绑定/审核流程生成，普通工具在可信内存 FastMCP 服务中执行；同名上游也由固定 Python 夹具定义并作为不同来源纳入正式 Catalog Provider，不运行用户 STDIO 命令。
- 参考 Host 的 HTTP 适配器只负责承载真实 MCP 调用和当前资源回调范围，没有伪造 renderer、初始 payload 或回调结果。同名上游回调名取自返回的来源中继壳，原上游 HTML 和 Prefab 动作仍使用 `save`。
- Catalog 来源 UUID 在夹具初始化时登记，读取请求不创建身份；资源/回调仍由正式来源 UUID、运行代际、当前 Key 和活跃来源校验。

## 浏览器操作与结果

### 上传 HTML 与 Prefab

1. 官方 SDK HTML 取得真实初始数据后发起合成 `store(value=7)` 请求。父页确认出现前真实工具计数为 0；批准后界面显示 `saved=7`，计数恰好为 1，后续 `unbound` 请求显示拒绝。
2. Prefab 0.20.2 的完整 bundled renderer 实际显示“真实调用联调”。通过原生 Tab/Enter 激活“测试写入”，父页批准后动态状态从“尚未调用”更新为“7”，不是只验证 structuredContent JSON 存在。
3. Prefab 慢调用批准后计数 `slow_started=1 / slow_cleaned=0`。关闭 Host 后 iframe 消失、父请求被 abort；实际 MCP 请求取消抵达上游，计数成为 `1 / 1`。不把移除 iframe 当作远端业务已回滚。

### 实际 Flutter 管理预览

使用已有脚本构建的后端 `/ui` 页面，打开同一个已审核 HTML App：默认模拟模式中，App 收到模拟返回但真实计数未增加。关闭后显式开启真实测试，App 发起请求，管理页实际显示来源 `preview-fixture`、工具/别名 `store` 和参数 `value: 7` 的确认框及副作用提示。点击“确认调用”后，子页显示 `saved=7`，真实计数只增加一次，未绑定工具被拒绝。

这组完成时合成计数为 `calls=2 / slow_started=1 / slow_cleaned=1`。模拟结果没有业务 `saved` 字段，夹具显示 `saved=undefined`，不能把它当作真实写入失败或成功；实际计数明确未增加。

### 两个同名上游，HTML / Prefab 四组

独立重建临时夹具后，`source-a` 和 `source-b` 都提供相同 `ui://shared/index.html` / `ui://shared/prefab.html` 和 UI-only `save(increment)`：

| 场景 | 实际界面 / 回调结果 |
| --- | --- |
| 来源 A HTML | 显示来源 A；批准后 `source=source-a; count=1`，unbound 拒绝 |
| 来源 B HTML | 显示来源 B；批准后 `source=source-b; count=1`，未串用 A 状态 |
| 来源 A Prefab | 原动态计数为 1；Tab/Enter 触发保存，经对应 opaque 回调批准后更新为 2 |
| 来源 B Prefab | 原动态计数仍为 1；保存后更新为 2，回调 opaque 名与 A 不同 |

最终夹具只读证据为 `sources={source-a:2, source-b:2}`，没有其他内存工具调用。关闭后确认页面显示 iframe 已释放。上游 HTML SDK 检查父 DOM/storage 均拒绝；Catalog 不通过改写脚本文本替换工具名。

该浏览器证据覆盖真实 Catalog MCP 下游和两个内存上游 Provider 的组合。外部 HTTP 代理工厂、双上游凭证、现代/legacy、停止/撤权/网络取消矩阵由 [Apps 安全矩阵](apps-security-matrix.md) 的真实 TCP 测试补充；不声称浏览器本身逐一执行了四种外部传输/OAuth，也不替代生产网络验收。

## 复现

在审核过的隔离依赖环境，分别从两个仓库目录执行。临时 bundle 目录可以替换；不要指向用户真实制品目录。

```bash
# frontend/apps-host
node test/build-reference-fixtures.js /tmp/mcpcat-reference-host-bundles

# mcpcat
PYTHONPATH=. MCPCAT_REFERENCE_HOST_BUNDLES=/tmp/mcpcat-reference-host-bundles python tests/browser/app_flutter_fixture.py
```

测试端口固定为 `127.0.0.1:18765`，不会绑定公网。浏览器访问 `/ui/reference-host` 选择六种夹具；访问 `/ui/#/apps/reference-app` 验证管理预览。父页确认前后通过 `/ui/fixture-evidence` 对比只读计数。参考 App 的初始化请求是其正常协议行为，不是注入点击或跳过用户确认；所有批准由父页可见按钮完成。

源码为后端 `tests/browser/reference_apps_host.py` / `reference_upstream_apps.py` 与前端 `apps-host/test/reference-*.js`。测试源码/页面不加入生产菜单，不在服务器执行上传脚本。完成后关闭浏览器标签和临时服务，TemporaryDirectory 清理测试数据库/制品。

自动化对 iframe 的直接坐标/定位器输入曾报目标不可用；实际通过标准键盘焦点进入 Prefab 并观察动态结果解决。未使用页面 evaluate 伪造点击/结果，也没有解除生产 sandbox 来获得通过。第三方 Host、真实用户数据、真实 Agent 账号和线上发布均不在此证据中。
