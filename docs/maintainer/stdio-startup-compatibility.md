# stdio 上游启动兼容

## 目标与边界

MCPCat 使用 FastMCP 公开的自动协议协商连接新旧 MCP 服务。本次处理的是上游工具
尚未启动就因 Python 依赖冲突退出的问题，不替换协议协商，不降级 MCPCat 的依赖。

## mcpdoc 规则

`mcpdoc` 旧版本从 `mcp.server.fastmcp` 导入 FastMCP，但未限制 `mcp` 大版本。
全新 uvx 环境可能解析到 MCP 2.x，导入失败后只剩 Connection closed。

- 仅识别已知 `uvx ... mcpdoc` / `uv tool run ... mcpdoc` 的包调用，不识别服务名称，
  不重写 shell 包装、自定义路径包或其他 Python 服务。
- 首次连接先在原环境执行 `mcpdoc --help` 检查导入。成功则原样运行，未来已修复的
  包也不会被强制降级。
- 只有非零退出且 stderr 同时包含 ModuleNotFoundError、mcp.server.fastmcp 时，
  才在工具自己的环境添加 `--with mcp<2`，验证成功后运行用户原来的业务参数。
- 自定义 `--with` 或明确的 UV 约束不覆盖；页面解释冲突，原始输出留在管理员日志。
- 配置原文不改写。每个代理工厂缓存成功的准备结果，并发连接共享准备锁；
  每个连接继续拥有独立 transport、进程、回调和身份上下文。
- 重启或重建服务后重新检查。依赖下载缓存由 uv 管理，不另建全局 Python 环境。
- `startup_compatibility: "off"` 可关闭该服务的自动准备与修复。

准备与锁等待占用同一启动预算，取消/超时回收准备进程。stderr 尾部最多 16000 字节，
按现有凭证集合脱敏，不记录完整 argv 或原始配置。uvx 环境继承方式与 SDK 一致。

## 测试台

工具、资源和提示模板发现允许使用启动预算，避免代理延迟启动上游时被原 25 秒
操作期限提前取消。普通业务调用仍保留原操作预算，不自动重放工具请求。
已知准备错误使用固定错误标识穿过 MCP 代理，HTTP 接口返回可读解释，避免传播原始秘密。

## 验收

```bash
.venv/bin/python -m pytest tests/test_stdio_compatibility.py tests/test_stdio_startup.py
.venv/bin/python -m scripts.probe_mcpdoc_compatibility
```

真实 Docker 探针使用临时数据库与随机测试凭证，保持用户原始 JSON，不携带生产配置。
验证冷安装、并发 READ/WRITE 会话工具发现、配置不改写、规则可查询，以及显式依赖约束
不覆盖且返回明确解释。探针发现 list_doc_sources、fetch_docs，不执行数据库或文档业务调用。
并发隔离夹具使用工具阶段屏障验证同时运行，避免以固定进程启动耗时推断并行性。

参考：[mcpdoc 上游问题](https://github.com/langchain-ai/mcpdoc/issues/67)、
[uv 工具环境](https://docs.astral.sh/uv/concepts/tools/)。

本次验收：后端全量 1440 项通过，补充兼容/取消专项 19 项及最终测试台回归 7 项通过。
Docker 原始 JSON 并发发现和显式约束冲突解释探针通过。
