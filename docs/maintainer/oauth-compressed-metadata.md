# OAuth 压缩元数据修复与验证

## 原因与修复

元数据读取的 `aiter_bytes()` 已由 HTTP 库解压正文。重建 Response 时保留上游的
Content-Encoding 会再次解压；原 Content-Length 也只对应线上压缩长度。
现统一移除这两个失效的头，使用解码正文重新生成响应长度，保留其他头与请求身份。

修复适用于共用读取路径的受保护资源元数据（PRM）、授权服务器元数据和 OIDC 发现，
不依赖特定 MCP 服务或包名，也不要求上游关闭压缩。解码器仍由 HTTP 库提供，
解压后正文仍受 256 KiB 限制，issuer/resource、HTTPS 和凭证隔离校验保持生效。

## 回归与真实验收

2026-11-20 验证：旧实现的 6 个正常压缩响应场景失败；修复后新增 14 项及全部
OAuth 相关测试共 246 项通过。原始 AsyncByteStream 压缩夹具覆盖 gzip/deflate、PRM、
授权服务器、OIDC 回退、UTF-8 正文、响应长度、损坏数据和解压超限。

```bash
.venv/bin/python -m pytest tests/test_oauth*.py -q
.venv/bin/python -m scripts.probe_oauth_discovery --url https://<上游主机>/<MCP路径>
```

探针使用临时数据库与随机本地测试 Key，通过真实 HTTP 添加服务、等待后台探测、
查询页面所用状态，检查 `auth_required`、持久化端点和 PKCE，再验证重新发现并移除服务。
指定的上游地址会收到无凭证的 MCP 探测及元数据 GET；不会注册客户端、登录用户、
兑换令牌或调用业务工具。临时服务配置在探针退出后清理。

用户反馈地址已分别在本机 Python 3.11 和 Docker Python 3.12 环境通过上述链路。
这证明发现与配置链路恢复，不表示已完成用户登录或 token 交换验收。
