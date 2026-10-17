# 管理与远程使用 MCP Apps

mcpcat 管理用户上传的界面或已配置上游提供的 App，由支持 MCP Apps 的客户端渲染。它不把上传的 Python/Node 当作后端执行，也不自动安装依赖或运行用户构建命令。本页描述升级代码，已通过官方 SDK 参考 Host 的实际浏览器交互；其他第三方 Host 未作产品认证，不表示已发布版本具备这些入口。

## 上传与预览

在 MCP Apps 菜单选择上传，填写稳定 slug、显示名和独立 SemVer，选择文件：

- Prefab JSON：由固定 0.20.2 模型校验，适合声明式组件、状态和工具动作。
- 单 HTML：适合已完成的自定义界面。
- ZIP：包含已构建 HTML/JS/CSS/附件；必要时指定入口。默认最多 512 文件 / 16 MiB，受管理员配置限制。不要上传需要 npm install/build 的源项目并期望服务器构建。

上传只创建草稿，原文件保持不可变并可下载。详情按版本浏览源码；修改需要新版本，不覆盖原始包。默认隔离模拟预览不调用真实工具，界面在 sandbox iframe 内运行；不能访问父管理页 DOM、storage 或认证 Key。

最小 Prefab 内容示例：

```json
{
  "state": {"count": 1},
  "view": {
    "type": "Column",
    "children": [
      {"type": "Heading", "content": "计数示例"},
      {"type": "Text", "content": "计数：{{ count }}"},
      {"type": "Button", "label": "增加", "onClick": {"action": "setState", "key": "count", "value": "{{ count + 1 }}"}}
    ]
  }
}
```

需要工具动作时，明确绑定已有 MCP 服务和工具别名，再检查当前输入 Schema。不要把整个服务目录作为默认授权。外部 connect/resource/frame 域名和 Host 权限需审核；不支持任意关闭 sandbox/CSP 的入口。HTML 附件装配不会在服务器运行上传 JS。

## 审核发布与真实测试

在草稿中核对工具绑定、CSP/权限、内容和变更说明，再审核发布。开启真实测试必须显式选择，逐次确认框显示来源与参数；只有确认后的调用才使用当前身份执行。关闭预览会取消可取消请求并清理通道，不能保证回滚已经完成或不配合取消的上游写入。

修改已发布 App 的内容、绑定或权限要创建新版本。撤销停止新资源读取与回调，但不能删除客户端已经收到的内容。模拟预览成功不是业务调用成功，也不是所有第三方 Host 已经兼容。

## 客户端连接

Apps 管理和远程分发固定提供，无需设置环境开关。使用 WRITE 身份查看 `GET /api/admin/settings/apps-mcp`，按需保存 path_name/require_auth。默认路径 `/mcp/apps`、需要 READ Key；修改路径后按 `restart_required` 提示重启，`ready=true` 才表示实际就绪。升级遇到旧服务占用默认地址时，系统自动持久化避让到 `mcpcat-apps`（必要时加数字后缀），不改旧服务地址；以实际接入信息为准。旧 `enabled` 和 `MCPCAT_APPS_ENABLED` 不再门控能力，旧草稿和私有内容也不会因此自动发布或公开。

详情的远程面板提供正确地址和固定版本打开示例，认证 Key 放在客户端安全配置中，不写进 App 文件或 URL。打开工具返回 UI 资源和初始数据，后续动作按当前身份、版本审核、来源状态和绑定逐次检查。普通 Host 仍可收到文字/数据回退，但不会因此变成交互界面。

已有上游 App 继续通过 MCP 服务配置接入，不必上传一份冻结副本。单服务保留原工具/资源；Catalog 为同名多来源建立独立资源和回调路由，UI-only 后端不进入普通搜索或 CodeMode。网关 Key 不作为上游凭证转交。

本地预览使用官方 Host bridge 2.0.3；它是受限管理预览，不是完整 Agent 产品。当前验证范围见 [参考 Host 实际交互](../maintainer/apps-host-verification.md)、[Apps 安全矩阵](../maintainer/apps-security-matrix.md) 和 [升级兼容证据索引](../maintainer/upgrade-evidence-index.md)。
