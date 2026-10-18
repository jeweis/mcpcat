# Python 最低运行版本兼容验收

> 安全依赖更新后的 Python 3.10 矩阵已扩展为 343 passed，28 warnings，包含新增安全下限检查及 OAuth 机器认证场景。安装采用冻结锁导出与哈希校验，详见 [安全修复补验](security-dependency-remediation.md)。以下保留首次最低版本验收历史。

最终审计发现项目声明 `requires-python >=3.10`，但 OAuth 使用了 3.11 的 `asyncio.timeout` / 内置 `BaseExceptionGroup`，生命周期也直接调用 3.11 的 `Task.cancelling()`。只在 3.11 测试会漏掉这些实际启动/鉴权路径错误，不能以 SDK 元数据支持 3.10 代替应用验证。

本次保持原最低版本，不增加生产依赖或提高 Python 门槛：

- OAuth 操作期限复用已有 AnyIO 的公开 `fail_after`，异常组使用已有 `exceptiongroup` 的公共兼容类型，不吞外部取消或将网络错误误判为无需认证。
- 3.11+ 的取消查询仍使用原生计数；3.10 查询 AnyIO 公共 TaskInfo，弱引用记录本服务已发出的取消，重复停止不会再次打断 finally 清理。不读取 asyncio/SDK 私有字段，不 monkey-patch 框架。
- 生命周期就绪等待使用 3.10 可用的 `wait_for/shield`，明确区分取消与超时；请求停止、后台任务和生命周期使用同一取消约束。底层副作用仍不能保证回滚。
- 测试中的协商/临时服务就绪期限也使用兼容接口；最终 3.10 矩阵没有为了绕过产品失败而跳过场景。

## 实际环境与结果

Python 3.10.19 安装于一次性工具目录，按现有 `uv.lock` 使用 `uv sync --frozen --no-install-project --extra dev` 安装；FastMCP 4.1.0、MCP SDK 2.3.0、AnyIO 4.12.1、Prefab 0.20.2、Monty 1.1.0。该 Python 分支按锁文件选择 Pydantic 2.12.5 / FastAPI 0.139.2。没有更换用户现用虚拟环境、默认 Python 或运行服务。

**263 passed，26 warnings，无 deselected/skipped**，覆盖：

- 新增 8 项跨版本超时/取消/异常组测试，包含重复停止不打断 finally、外部取消不变成 timeout。
- MCP 生命周期、现代/legacy 与重定向 OAuth 协商、授权/注册/刷新/回调/竞态/安全日志。
- 单时代上下游组合、CodeMode 默认配置/目录/沙箱、官方 Skills 方法/网关与旧 1.0.1 数据升级/恢复。

复现（使用自己的临时环境 Python）：

```bash
python -m pytest -o addopts= -q --disable-warnings tests/test_async_compat.py tests/test_mcp_service_lifecycle.py tests/test_oauth*.py tests/test_proxy_cross_generation.py tests/test_code_mode_provider.py tests/test_code_mode_settings.py tests/test_code_mode_sandbox.py tests/test_remote_skill_protocol.py tests/test_skills_mcp_gateway.py tests/test_v101_data_upgrade.py tests/test_v101_upgrade_recovery.py
```

原 Python 3.11.14 环境完整后端 **785 passed，70 warnings**，真实 TCP 取消/双上游授权与 Apps/Skills 安全回归也通过。3.10 是上述定向矩阵，不冒充在 3.10 跑过所有 785 项；没有执行 Linux/Windows 全平台或完整 Docker 镜像运行验收。容器声明仍为 Python 3.12，独立版本门禁校验最低版本与容器基线漂移，不把安装元数据等同于跨平台运行认证。
