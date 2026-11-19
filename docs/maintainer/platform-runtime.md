# 平台 MCP 运行管理设计与验收

## 模块

- `runtime_environments.py`：OS 文件锁、HMAC 配置身份、原子修订、校验状态和回滚。
- `process_supervisor.py`：透明 stdin/stdout/stderr 子进程监督，独立私有文件记录 pid/退出码。
- `runtime_process_probe.py`：Linux 有界只读观察进程树，定位活动 uv/npx 环境，不记录完整 argv。
- `runtime_attempts.py`：每连接独立尝试、阶段、stderr 尾部、根因分类与脱敏记录。
- `observed_transport.py` / `runtime_http.py`：公开 transport/client factory 装饰，保留认证与 TLS 行为。
- `runtime_client.py`：公开 Client 上下文、close、mode 的有界初始化恢复，不改 SDK 私有状态。

所有来源共用状态与分类规则。mcpdoc 原规则保留为已验证插件；通用 uv 旧 SDK 导入恢复
不要求包名为 mcpdoc。原生命令和远程来源不虚假宣称具有本地包隔离或完整依赖锁定。

## 并发与持久化

每次工厂调用选择当前修订，每个连接持有稳定修订对象。刷新指针后旧连接仍写回自身历史修订，
不能覆盖当前指针。多个 worker 通过同一数据目录的文件锁共用修订；缓存错误只对当前失败
修订旋转一次。配置或本地脚本/工具链身份改变后创建新的兼容域，不混用之前的环境。

只持久化安全摘要、分类、版本和配置 HMAC；HMAC 密钥及清单使用私有文件权限。
原始 stderr 有界保留于诊断存储并脱敏；stdout 不加任何非协议内容。
SDK 管理进程组/Job Object，监督器将子进程退出报告与 SDK 主动关闭区分。
取消和超时释放锁并回收进程，业务请求不进入恢复循环。

包缓存复用不是跨平台完整 lockfile。包源、依赖约束或缓存显式配置保持原意；
Linux 能定位活动包环境时读取该环境的版本，否则明确返回缓存清单。
当前快照最多 200 项。多服务依赖缓存按服务、配置和工具链分域。

## 管理接口

所有接口 `/api/admin/*` 强制 WRITE，匿名 401、READ 403，响应 no-store。

- `GET /api/admin/runtime`：各来源运行摘要。
- `GET /api/admin/runtime/{name}`：环境修订及最近尝试。
- `POST /api/admin/runtime/{name}/environment`：空 JSON 创建新修订；`revision` 回滚已验证同配置修订。

网页 `/runtime` 提供同样的查询、刷新和回滚操作，READ 身份不发起管理查询。

## 复验

```bash
.venv/bin/python -m pytest tests/test_platform_runtime.py tests/test_stdio_startup.py
.venv/bin/python -m scripts.probe_platform_runtime
.venv/bin/python -m scripts.probe_platform_runtime --real-packages
```

前两个命令使用标准库旧协议夹具、真实 Node、现代 HTTP、SSE 与临时数据；
第三个命令还会从包源安装真实 Context7 4.7.3 和 mcpdoc，适合有网络的隔离 Docker。
所有探针使用随机测试凭证，不连接用户私有数据库，不执行有业务副作用的工具。
SQL 登录失败和缺失驱动按通用 stderr 分类验证，不宣称已验证用户 MSSQL 实例的可达性。

浏览器脚本 `frontend/tool/test_runtime_management_browser.mjs` 验证版本摘要、新修订、回滚、
尝试详情、远程来源、390px 布局和只读身份拦截。它只接受一次性验收实例的测试凭证。

2026-11-19 验收通过：后端全量 1454 项、最新相关回归 82 项、前端全量 174 项，
以及最终浏览器和 Docker 探针。Docker 使用非 root 应用用户、Python 3.12、Node 20；
每次探针使用空的临时运行目录，覆盖冷启动、并发 READ/WRITE 会话、工具发现和环境回滚。
Context7 记录到活动依赖 103 项；mcpdoc 无法定位单一活动环境时明确返回缓存清单，
不将其中的多版本依赖解释为当前版本。测试未访问用户私有 MSSQL 或 MySQL。
