# 一个 Time MCP 配三个 Skills、两个 Prompt 和一个 App

三个 Skill 均只有 `SKILL.md`，没有脚本、凭证或外部账号要求：

| Skill | 示例提问 |
| --- | --- |
| [world-clock](world-clock/SKILL.md) | 上海、伦敦和纽约现在分别几点、几号？ |
| [meeting-overlap](meeting-overlap/SKILL.md) | 比较上海与伦敦今天的工作时间，找出 30 分钟会议的共同窗口；已过去的时段请标注。 |
| [deadline-localizer](deadline-localizer/SKILL.md) | 上海今天 23:00 截止，在伦敦对应几号几点？ |

## 配置与上传

演示的 `server.py` 通过公开 MCP 组合接口代理官方 `mcp-server-time==2026.10.10`（`uvx` STDIO，参数 `--local-timezone=Asia/Shanghai`），保留 `get_current_time` 和 `convert_time` 原名称及参数，并附加以下内容：

| 入口 | 名称 | 用途 |
| --- | --- | --- |
| 提示模板 | `meeting_planner` | 输入双方时区与会议时长，获取会议安排提示 |
| 提示模板 | `deadline_localizer` | 输入双方时区与截止时间，获取跨日换算提示 |
| 应用 | `open_world_clock` | 可选择时区、通过同一连接查询真实时间的世界时钟 |
| 资源 | `guide://time-demo/usage` | 使用说明，Markdown 正文只读 |

mcpcat 服务名仍为 `time-demo`，启用认证；客户端无需安装上游包。Prompt 是消息模板，不会自动调用模型或工具。App 的 HTML 为仓库中的固定可信示例，不依赖 CDN，不携带凭证；工具调用由宿主通过当前 MCP 连接转发。它不是上传 Skill 的服务器执行器。

本示例固定的旧版官方时间上游使用 legacy 协商，连接由演示服务生命周期明确持有并复用，退出时统一关闭，避免目录发现期间重复启动进程。STDIO 关闭隐式 keep-alive，退出后不遗留无人管理的进程。mcpcat 对 Agent 的新旧协议支持不变，通用网关仍为各跳独立协商，不跨身份共享认证连接。不要将此演示的已知版本设置套用到任意上游。

在包含本示例的 Docker 镜像中，将现有 `time-demo` 的 command 改为 `python`，args 改为 `["/app/examples/time-skills/server.py"]`；保留原来的其他配置。非 Docker 部署使用示例脚本的实际绝对路径，并确保运行环境已安装项目依赖和 `uvx`。修改前备份原配置；回退时恢复原 `uvx` command 与原 args 即可，服务名不变就无需重绑 Skills。

打开「测试」页面：在「提示模板」选择并填参数后读取；在「应用」选择 `open_world_clock`，启用「真实工具测试」，再打开隔离预览。应用内点「查询当前时间」时确认只读调用，可看到真实时间；不开启真实测试时只返回模拟结果。应用入口只是打开界面，不自动查询。

在 Skills 页面上传上面任一 Skill 子目录，选择“两种方式”，绑定 `time-demo`，审核后发布。每个子目录是一个独立 Skill，不要把本演示父目录当成一个 Skill 上传。三个 Skill 可以同时绑定同一个 MCP 服务。

客户端连接当前部署的 `/mcp/time-demo/`，通过认证头传入 READ Key，不能把凭证写进 Skill。支持远程 Skills 扩展的客户端可在该连接发现并读取三个 Skill，再调用同一连接的时间工具。不支持远程扩展的客户端可下载或用 CLI 安装 Skill，同时配置该远程 MCP。

服务器保留上传内容，不执行 Skill。此处的时间查询由 MCP 工具执行，会议窗口比较由 Agent 完成。

## 工具边界

该版本 `convert_time` 只接收 HH:MM，不接受日期，按来源时区今天转换。不能用它的结果验证明天或夏令时切换后的日期。Skills 明确要求其他日期使用客户端已有的 IANA 日期计算能力，或告知限制，不假装工具支持未来日期。不要把示例固定时间当成当前可预约时段。

## 实测记录（2026-10-16）

在已有本地实例新增 `time-demo`，三个 Skill 的 `0.1.0` 上传并发布为 local+remote，未修改已有资源。通过带 READ Key 的远程协议请求验证同端点三个 Skill 的 list/get/read、文件 size/SHA-256，以及实际时间工具调用；匿名读取被拒绝。服务器不含 Skill 脚本。

这证明协议读取与工具调用链路，不代表已验证任意第三方 Agent 自动选择/激活 Skill。使用者可在 Skills 管理页撤回或删除演示资源，并停止/删除 `time-demo`；不要删除其他服务。

早期曾发现连接状态接口引用旧平台 `enabled` 字段导致 `ready=false`；该问题在后续固定提供能力改造中修复。此段保留为历史记录，不以状态文字代替真实协议验证。

上游参考：[官方 Time MCP](https://github.com/modelcontextprotocol/servers/tree/main/src/time)。
