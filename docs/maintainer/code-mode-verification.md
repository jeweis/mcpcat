# CodeMode 验收记录

本记录对应 `upgrade-fastmcp-v4-and-official-skills` 的 Gate Code，不代表整个升级提案已完成，也不表示已发布或替换现有运行服务。

## 固定测试环境

用户操作见 [CodeMode 使用指南](../user/code-mode.md)。后续任务 8.7 的两项服务/Catalog 参数化回归已验证：缺失配置不回写默认值，SQLite backup 恢复保留有限额/空允许列表/未知字段，开启或关闭 CodeMode 后重新生成仍幂等、版本 ID 不变，既有 local/remote 绑定、元数据及 ZIP 原字节不变。完整旧程序恢复与升级后新数据保留见 [恢复演练](upgrade-recovery-drill.md)。这些结论不替代真实第三方 Host 验收。

Python 3.11.14、FastMCP 4.1.0、MCP SDK 2.3.0、pydantic-monty 1.1.0。普通 FastMCP Client 分别使用 `auto`（现代协议）和 `legacy`；后者是该 SDK 的旧协议模式，不是对所有历史 SDK 或第三方 Agent 的认证。

`tests/test_code_mode_upstream_auth.py` 的 4 个回环 HTTP 用例使用实际应用工厂、MCPServerManager、MCPServerFactory、代理客户端和 Monty；没有替换业务代理工厂。两个可信测试上游均暴露 `value`，分别要求独立的合成 Bearer 凭证。数据库、配置和制品均使用临时目录，不启动用户配置的程序。

## 已验证边界

| 边界 | 证据 |
| --- | --- |
| 普通现代/legacy 客户端 | search、get_schema、execute，真实结果组合，原 Catalog 同时保留 search_tools/call_tool |
| 同名多来源 | alpha__value / beta__value 分别执行对应来源；不存在猜测或最后一次匹配路由 |
| 上游权限 | 上游中途撤权后后续调用拒绝、重新搜索隐藏该来源，另一来源仍可调用 |
| 凭证隔离 | 上游收到各自配置凭证，未收到 Mcpcat-Key；不同网关 Key 不替换上游凭证 |
| 隐藏工具 | 范围外名称直猜不能执行业务；明确不匹配的 lookup 在查询上游描述前拒绝，仍保留完整目录冲突排除 |
| 当前身份 | 撤销 Key、停用来源、配置变化及来源重建均不能沿用旧执行权限 |
| 多身份配额 | 相同 Key 达到执行并发限制时拒绝；另一有效 Key 仍可执行；客户端取消后可重新获得槽 |
| 调用次数 | 超额第二次调用不分发，返回 completed 1/2 与不回滚说明 |
| 取消 | 两种客户端均通过真实网络取消，清理网关任务并传至可取消上游；不可确认取消的调用保留配额直到结束 |
| 受限沙箱 | 有限 CPU/内存、工具并发/超时、总 deadline；不能读取宿主文件、shell、网络或上传 Skill |
| 旧行为 | 缺失配置默认关闭且不回写；原 Catalog 路由、Skills 工具绑定与下载不自动改为 CodeMode |

READ/WRITE 是现有全局权限，不在本次升级中虚构新的逐用户服务 ACL。CodeMode 进一步缩小原 Catalog 纳入范围；已有 OAuth 来源仍按原规则排除，不借用其他用户的 OAuth 会话。UI-only、Apps 打开/回调及网关递归元工具不进入编排目录。

## 取消链路修复

真实客户端验收发现：固定 SDK 的现代 JSON-only 响应分支不监听断连；只取消 Python 层的模拟请求不能证明网络取消有效。CodeMode 改用公开 `http_app(path="/")` 的原生流式路径，而非 `json_response=True`。SDK 处理现代关闭请求和 legacy cancellation 的差异，mcpcat 不自行实现协议取消方法。

可取消测试上游也使用原生流式路径。上游若忽略取消、不支持取消或已完成写操作，网关不能保证撤销其业务副作用；测试不把释放本地连接等同于远端回滚。不存在自动重试整段代码。

## 复现与结果

在审核过的隔离依赖环境、`mcpcat/` 目录执行（回环用例需要允许监听本机临时端口）：

```bash
python -m pytest -o addopts= -q tests/test_code_mode_settings.py tests/test_code_mode_provider.py tests/test_code_mode_sandbox.py tests/test_code_mode_gateway.py tests/test_code_mode_upstream_auth.py
python -m pytest -o addopts= -q --disable-warnings
```

本轮 CodeMode 共 63 项纳入完整回归；完整后端结果 **660 passed，27 warnings**。Gate Code 按上述固定版本和范围通过，任务 5.14/5.18 完成。真实第三方 Apps Host、公共 npm CLI 全流程、完整升级恢复演练、Web 构建同步和其他未勾选任务仍单独验收。
