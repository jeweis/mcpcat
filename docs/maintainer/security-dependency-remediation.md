# 生产依赖安全修复与补验（2026-10-18）

## 结论与范围

此前候选镜像的 Python 依赖安全阻塞已修复。重新构建的本地镜像 `mcpcat:security-fixed-20261018` 实际安装 108 个 Python 包，pip-audit 报告 **0 条已知漏洞、0 个跳过包**，未设置漏洞忽略名单。此结论是当次 Python 公告库审计，不代表操作系统、Node.js 或公网渗透安全认证。

本轮未提交、推送、创建 Tag、发布 npm/Docker 或归档。原 `localhost:18081` 容器及其数据库、制品和数据卷未替换；浏览器最终补验使用独立的 `127.0.0.1:18082`、一次性数据库与合成凭证。

## 修复与兼容约束

- requirements 与 pyproject 同步安全下限：PyJWT 2.15.1、Pygments 2.21.0、AnyIO 4.15.1、cryptography 50.0.2、pydantic-settings 2.14.2、python-dotenv 1.2.4、requests 2.34.2、urllib3 2.8.0；定向更新 uv.lock，不批量升级框架。
- pydantic-settings 保留 Python 3.10 分支：锁文件在 3.10 选择 2.15.0，较新 Python 选择 2.16.0，没有提高最低 Python 版本。
- Docker 显式固定 pip 26.2、uv 0.13.1，并拆开 PNPM_HOME 与 PATH 声明以消除构建警告。
- SSE 连续生命周期回归发现 sse-starlette 3.2.0 的全局退出状态会影响后续连接，定向更新到 3.5.0。上游问题及修复见 [issue 211](https://github.com/sysid/sse-starlette/issues/211) 和 [3.5.0 release](https://github.com/sysid/sse-starlette/releases/tag/v3.5.0)。没有修改 SDK 私有状态或重置全局标志来掩盖失败。
- 保持 FastMCP 4.1.0、MCP SDK 2.3.0、数据库版本及历史 ZIP/CLI 制品兼容约束，不改写已有数据或重新生成历史制品。
- 新增 10 项依赖基线测试，检查两种安装声明、全锁 Python 分支、Docker 工具固定版本及最低 Python 版本。
- Python 3.10 验收还发现三个测试夹具使用了 3.11 专属 asyncio.timeout，已改为产品已有 operation_timeout；未放宽期限、跳过场景或修改业务断言。

## 回归结果

| 验收 | 结果 |
| --- | --- |
| 最终锁定 Python 3.11 环境，全量后端 | 1333 passed，386 warnings，309.40 秒 |
| Python 3.10.19 最低版本兼容矩阵 | 343 passed，28 warnings |
| 修改后的三个测试夹具与安全基线，Python 3.11 补验 | 30 passed，23 warnings |
| 最终镜像真实协议、Skills/Apps、旧数据与恢复专项 | 84 passed，19 warnings |
| 已发布 npm CLI 1.0.1，真实升级兼容探针 | 10 Agent × 2 Scope，200 次命令通过；远程专用 Skill 被正确忽略 |
| 当前 CLI | 129 passed；类型检查、Lint、构建、包内容检查与 tarball 安装通过 |
| Flutter | 148 passed |
| 兼容漂移检查 | 14 passed；版本一致性与 Gate C 文档检查通过 |
| 实际镜像启动与权限 | UID 1000；healthy；匿名访问受保护接口被拒绝 |

最初锁定环境全量回归为 1331 passed、2 failed，失败涉及 legacy SSE 撤权和撤权中的输入连接。没有直接把单次重试视为全量成功：两个文件按原断言与时限固定复验三轮，每轮 6 passed；停止并发构建/安装后，最终串行全量为上表 1333 passed。测试警告仍保留，不宣称零告警。

## 浏览器 App 真实查询

最终镜像中注册仓库自带的 time-demo 示例；使用 Default Read Key 登录测试台，打开 Open World Clock 隔离预览，启用逐次确认的真实调用。实际点击 App 内“查询当前时间”，宿主展示当前端点、工具 get_current_time 和 Asia/Shanghai 参数，确认后 App 显示“查询完成”并返回：

```json
{
  "timezone": "Asia/Shanghai",
  "datetime": "2026-10-18T15:41:40+08:00",
  "day_of_week": "Sunday",
  "is_dst": false
}
```

这是浏览器内嵌 App 发起的真实工具调用，不是模拟结果或只用 API 代替点击。验收后关闭预览并清理自建临时容器；原服务不变。

## 证据与发布边界

本地证据位于 `/private/tmp/`：`mcpcat-security-backend-serial.log`、`mcpcat-security-py310-final.log`、`mcpcat-security-locked-protocol-final.log`、`mcpcat-security-published-cli.log`、`mcpcat-security-frontend.log`、`mcpcat-security-image-final-audit.json`、`mcpcat-security-docker-final-build.log`、`mcpcat-security-fixed-app-query.png`。临时证据可能被系统清理，本页保存结果与边界。

仍需按新统一版本执行正式发布流程，不能复用已经发布的 npm 1.0.1 / v1.0.1 Tag。本轮没有把全部手工门禁新增到 CI，也没有再次重复此前通过的 Apps Host 24 项和文件选择插件 3 项；其证据仍见 [修复前验收记录](production-preflight-20261018.md)。既有 Flutter 静态分析警告/提示未夹带清理。
