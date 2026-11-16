# 网页网络诊断设计

## 范围

补全诊断日志的自动刷新、导出，并提供管理员网页网络诊断。现有 WRITE 权限可运行
Ping、DNS、TCP 和 HTTP HEAD；READ 和匿名请求不能执行，也不能读取命令能力。
命令在服务端运行，能够检查容器网络与上游地址，包含内网目标。

## 实现

- `NetworkCommand` 校验结构化参数，拒绝 Shell、额外选项和带认证信息的 URL。
- `command_args` 构造固定 argv；所有进程经 `create_subprocess_exec` 启动。
  DNS/TCP/HTTP 使用独立标准库脚本，无额外依赖；Ping 使用系统程序。
- `NetworkCommandRunner` 每 worker 最多并行 2 个进程，不共享进程、输出或用户状态。
- `POST /api/admin/commands/run` 返回 NDJSON 实时输出；认证 Key 仅在应用配置的头中传递。
  `GET /api/admin/commands` 返回执行位置、ping 可用性及限制。
- 按完整行脱敏，超过 4096 字节的行整体省略；原始输出 64 KiB、最长执行 60 秒。
  停止、断开、取消和超时均终止进程并排空管道，避免满管道导致回收挂起。
- POSIX 使用独立进程组，Windows 仅运行不派生子进程的诊断程序。
- 浏览器使用 XHR 文本进度事件接收输出，避免 Dio 默认 Web adapter 缓冲整个响应；
  原生端使用 Dio stream。身份切换或页面销毁会取消请求，不自动重试命令。
- 日志自动刷新默认关闭，开启后每 5 秒加载最新页；保留当前内容直到响应到达。
  导出仅包含当前已加载且已脱敏的日志，不声称导出全部历史。

## 运维

反向代理需关闭此流式接口的响应缓冲，服务也返回 `X-Accel-Buffering: no`。
连接意外结束但没有最终事件时，页面显示结果未确认。诊断不能替代实际 MCP 协议握手。
日志仍为每进程最近 1000 条，重启清空，不读取历史容器日志。

## 验收

- 后端全量 1416 项、前端全量 170 项通过，变更文件 Dart 静态分析无问题。
- 专项测试使用真实子进程验证 DNS、TCP、HTTP、退出码、环境隔离、跨块脱敏、
  超长行、64 KiB 输出上限、超时、取消与两进程并发限制。
- 临时真实 HTTP 实例验证回环 Ping、DNS、TCP、HTTP HEAD，断开后进程回收，
  以及匿名 401、只读 403；Docker 中以非 root 的 app 用户重复验证通过。
- Web 正式构建成功；浏览器验收覆盖 Ping 结束前收到输出、停止、进程回收、
  日志导出与自动刷新、390px 窄屏、只读页面拦截。

复验命令（后端目录，无需生产凭证）：

```bash
.venv/bin/python -m scripts.probe_network_commands
```

浏览器脚本为前端 `tool/test_network_commands_browser.mjs`，只接受临时实例的测试凭证。
