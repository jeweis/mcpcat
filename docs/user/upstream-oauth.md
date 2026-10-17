# 上游 MCP 的 OAuth 授权

本文描述已实现并完成本次验收的上游授权行为，已部署至本机 Docker，公共版本尚未自动发布。已有 MCP 地址、手动填写 client_id/client_secret 和自动/手动回调入口保留；请勿仅因发现成功就认为授权或业务调用已经成功。验收范围见[最终验收](../maintainer/mcp-final-acceptance.md)。

## 连接与发现

添加 HTTP MCP 服务后，网关用框架公开客户端先协商协议：现代服务使用 server/discover，旧服务由 SDK 回退握手。SSE 使用其旧传输。探测不调用业务工具，也不会自动打开服务器上的浏览器。

同一 origin 的尾斜杠等路由重定向继续可用。协议探测拒绝跨 origin、HTTPS 降级或带用户名/密码的跳转，避免配置的自定义认证头被发送给另一来源；需要迁移地址时，由管理员明确修改服务配置。

只有上游实际返回 401 时才进入授权发现。网关先读取 WWW-Authenticate 声明的受保护资源元数据，再按规范尝试路径级、根级 well-known；授权服务器可以与 MCP 服务器不同。随后发现授权服务器元数据或 OIDC 文档，校验 resource 与 issuer。没有现代资源元数据时，仍兼容旧根目录授权发现。

发现阶段不会把网关 Key 或配置的上游 headers 发送给元数据服务器，不自动跟随元数据重定向。自动发现地址必须使用 HTTPS，开发回环 localhost/127.0.0.1/::1 可使用 HTTP；拒绝 URL 内嵌用户名/密码、片段和非法端口。元数据单份最多 256 KiB，读取和整次发现均有期限。不要通过关闭 TLS 验证来解决发现错误。

## 继续原授权流程

需要授权时，使用服务原有授权入口。管理 API `POST /api/oauth/<服务名>/authorize` 优先使用已有 client_id；没有身份时，先考虑明确配置且来源声明支持的 CIMD，再尝试原 DCR。无法注册或已绑定 issuer 不匹配时，继续返回 `needs_credentials=true`，由管理员核对注册信息或填写新凭证，而不是假定所有来源都支持自动注册。

已有手工凭证、回调模式、scope 和未知配置字段不会被后台发现覆盖。已存 token/refresh token 不在本次发现阶段被删除或重新授权。DCR/CIMD 及回调/刷新已通过本地兼容夹具，不代表每个第三方授权服务器都已逐一验收；不要仅凭元数据中有 CIMD 标记就假定配置完成。

自动回调和手动粘贴回调仍使用原入口。回调 URL 中的 code/state 属于敏感授权数据，请勿公开分享、提交 Git 或贴入问题日志；client_secret/token 只通过授权配置和安全存储提供，不写入 Skill 或 MCP 连接 URL。

只读账号查询服务器配置时，`oauth` 返回 `null`，不返回客户端密钥、令牌、私钥引用或其他 OAuth 扩展字段；这表示配置内容受限，不代表上游没有启用 OAuth。服务授权状态仍查看状态接口或测试台授权诊断。管理员（WRITE）保留完整配置读取和编辑能力，已有数据库配置不因只读查询被清除。

同一只读配置响应也不提供来源 URL、认证头、环境变量、启动命令/参数及未知配置扩展，仅保留类型、名称、启用状态、超时、认证要求、备注、标签和 Catalog 纳入标志。备注和标签属于公开描述，请勿填写秘密。需要编辑或复制完整执行配置时使用管理员账号；客户端连接仍使用 mcpcat 对外端点，不需要读取上游凭证。

每次授权冻结当时的来源与配置，state 五分钟有效且只使用一次；重新发起同一服务授权会替换上一尝试。授权服务器声明需要 `iss` 时，回调必须携带匹配 issuer；未声明必需但提供了 `iss` 时仍检查匹配。没有 issuer 绑定的旧手工配置继续允许原无 `iss` 流程，但不会盲目接受未知 issuer。

手动粘贴的回调必须对应本次显示的 redirect URI；重复 code/state/iss、授权错误或来源/凭证变化会拒绝。交换期间发生同名重建、来源变更或保存失败，不会将 token 写给新来源或显示成功；请重新授权，不要重放旧回调。

授权成功后，每次新上游连接读取当前 token，刷新、更换、过期或移除不再依赖重建代理才生效。明确记录的 issuer 或 token 对应 client_id 不匹配时不发送凭证。旧记录缺少新绑定字段时保留兼容，不在升级时强制所有用户重新授权。后台刷新也会在请求前后校验来源与配置，不覆盖已停止、重建或重新配置的服务；保存失败不会宣称已刷新。

通过 `set-credentials` 明确换 client_id/secret 时，会停止使用该配置中的旧 token；换 client_id 而不提供 secret，不继承前一个 client 的 secret。未变更的旧身份不会因升级被重注册。DCR 来源的回调地址变化时，应先在授权服务器更新允许的回调或取得新客户端凭证。

## 可选 CIMD 身份

默认不强制 CIMD。若希望使用，在原 MCP 服务的 OAuth JSON 配置中增加 `client_metadata_url`，例如 `https://client.example/oauth/mcpcat-client.json`。该地址是公开客户端身份，不是秘密，也不是 mcpcat MCP 地址。已有 client_id 会继续优先使用；切换身份需明确编辑配置并重新授权。

将下列示例作为静态 JSON 发布到自己控制的 HTTPS 地址，`client_id` 必须与实际文档 URL 完全相同：

```json
{
  "client_id": "https://client.example/oauth/mcpcat-client.json",
  "client_name": "My mcpcat",
  "redirect_uris": ["http://localhost:8765/callback"],
  "token_endpoint_auth_method": "none",
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"]
}
```

示例用于手动回调；自动模式应改为该服务实际显示的 `https://<mcpcat-对外地址>/api/oauth/callback/<服务名>`，有多个服务时声明相应回调。文档不能包含 client_secret、token 或网关 Key。授权服务器负责获取和验证 CIMD 文档，mcpcat 不执行它，也不自动替你发布它。

只有来源实际声明 `client_id_metadata_document_supported=true` 且配置了合法非根路径 HTTPS 文档 URL，才会采用 SDK 的 CIMD 身份（无 client_secret、token 认证方式 none）；不要伪造能力标记。没有配置或来源未声明支持时仍可使用 DCR/预注册，不会强制迁移原身份。CIMD 身份可按 SDK 规则用于不同 issuer，但已发 token 不会因此跨 issuer 复用。参见 [FastMCP CIMD 说明](https://gofastmcp.com/clients/auth/cimd)。

## 机器认证（无需用户登录）

无需用户浏览器登录的上游，在新增或编辑 HTTP/SSE 服务时点击「配置上游认证」，选择「机器认证（无需用户登录）」。填写授权服务器 Issuer、令牌端点、客户端 ID、凭证和允许范围，点击「应用到服务配置」后，再保存服务。应用只修改当前表单，取消不会保存；保存后重连上游并申请短期令牌。

凭证方式可选密钥 Basic、密钥 POST 或私钥 JWT。后者只填写服务端私钥环境变量名和 RS256/ES256 算法；私钥由部署环境注入。OAuth 凭证不会写入新增服务的浏览器草稿。完整 JSON 配置入口仍可使用 `grant_type: client_credentials`，支持字段如下：

- 必填：`issuer`、`token_endpoint`、`client_id`、`scopes`（字符串数组，明确允许范围）。地址使用最终 HTTPS 地址，本机测试允许回环 HTTP。
- 密钥方式：`token_endpoint_auth_method` 为 `client_secret_basic`（默认）或 `client_secret_post`，通过原安全配置渠道提供 `client_secret`。
- 私钥方式：`token_endpoint_auth_method: private_key_jwt`，`private_key_env` 指向 mcpcat **服务端**环境变量，变量值为未加密 PEM 私钥；`signing_algorithm` 为 `RS256`（默认，RSA 至少 2048 位）或 `ES256`（P-256）。配置只存变量名，不要把私钥正文粘贴到 JSON、页面或日志；部署时由秘密管理系统注入环境。

先在授权服务器登记对应客户端/公钥。断言由 SDK 创建，有效期 60 秒，audience 使用授权服务器 issuer；不允许自定义 claims 或共享密钥 JWT 算法。更新服务端私钥环境后需按部署方式重建/重启进程，使新连接获得新密钥。

机器认证不与静态 Authorization 头混用，不使用旧交互式 token。令牌在单个客户端内缓存，不写回来源配置；新连接重新申请。scope 挑战超出配置、令牌地址变化或令牌响应重定向都会拒绝，不自动扩大权限或跳到另一个地址。此机制是 **mcpcat 连接上游**的身份，不改变用户连接 mcpcat 的 Key，也不是把用户凭证透传给上游。

机器认证来源调用旧浏览器授权或“设置凭证后登录”接口会返回 409，并提示编辑服务端认证配置；不会自动改成用户登录流程。切换认证方式后，之前发起的浏览器授权回调失效，迟到令牌不会覆盖新配置。

服务卡片显示机器认证状态，失败时到「配置上游认证」检查设置，或在测试台打开「连接与授权诊断」进行显式探测。原有用户浏览器授权在对话框选择「保留现有配置 / 浏览器授权」，不会因为打开编辑器而清除历史令牌和扩展字段。

## 日志注意

应用默认 Uvicorn access logger 对自动回调只保留路径与状态，查询部分会脱敏；业务错误日志不输出 state/code、token 请求响应正文。反向代理、外部日志平台或自定义日志配置不由该过滤器控制，也需要关闭或脱敏 OAuth 回调查询。不要在排障时开启含凭证的完整 HTTP 报文日志。

## 排障

- `auth_required`：发现了可用 OAuth 元数据，继续授权，不表示已取得 token。
- `auth_unsupported`：收到 401，但没有发现可用元数据。检查来源是否使用静态认证或需手动提供 OAuth 配置。
- `timeout` / `connection`：先检查网络、来源地址和传输，不是“无需认证”。
- `protocol_version` / `protocol`：协商未成功，不应通过反复授权来解决；检查来源协议/传输和服务日志。
- `oauth_metadata...`：检查资源/issuer 绑定、HTTPS、元数据大小及可达性；不要更换成未经审核的授权服务器。

后台探测失败时，服务显示失败及固定安全诊断，不再误报就绪。通过授权入口主动探测失败会返回 502；不会把原始响应正文、凭证或完整来源 URL 放入新增诊断。服务被同名重建后，旧探测结果不会覆盖新来源。
