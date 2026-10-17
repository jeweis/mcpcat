# HTML 与 Prefab 装配验收（9.6）

本记录覆盖任务 9.6 的 Prefab renderer/动态绑定和 HTML/附件装配。计算型 import、作者 import map、模块查询参数及 sandbox 验收已补齐；上游 Catalog 聚合、完整 Apps 安全矩阵和真实第三方 MCP Apps Host 仍分别由 9.8/9.12/9.13 验收，不据本记录宣称整个升级可发布。

## 静态装配边界

固定依赖 `tree-sitter==0.25.2`、`tree-sitter-javascript==0.25.0`，同步 requirements、pyproject 和 uv.lock。解析器只读取 UTF-8 字节与语法树，不运行 Node/Python 用户程序、不执行 JS、不安装上传包依赖，也不调用作者构建命令。解析限制为每次一秒，v5 模块图整体装配预算十秒；输入受原包 512 文件/16 MiB 限制，派生模块和 HTML 另有 64 MiB 容量限制。

选择语法树而非全文替换，以免将注释、普通字符串或正则中的 import 字样误当作模块引用。相关公开资料为 [Python 绑定](https://tree-sitter.github.io/py-tree-sitter/) 和 [JavaScript grammar](https://github.com/tree-sitter/tree-sitter-javascript)；实现使用上述固定版本，不直接追随文档最新版本。

模块图只从已验证的包内字节读取模块，路径复用包边界和已审核 HTTPS origin 检查，不从服务器下载外部模块。模块使用包内容摘要与路径生成 `mcpcat-app://` 私有虚拟身份，通过原生 import map 映射为 data URL；该身份不是新 HTTP 端点，也不含 API Key。先登记模块再处理依赖，循环不会递归展开。v4 使用确定身份注释；v5 将完整虚拟 URI 编码到 data URL fragment，保证同模块共享实例、相同字节不同路径分别执行、不同 query/fragment 不错误合并，并让 import.meta.url 保留实际虚拟身份。

已实现外部/内联模块入口、静态 import/export、字符串/变量/template/嵌套表达式动态 import、modulepreload、静态/计算型 `new URL(附件, import.meta.url)`。原生 script 标签保留 module/async/id/onload 等属性；SRI 验证原始字节，import map 在第一个模块/预载前交付。计算表达式只求值一次，动态对象引用按字符串提示转换；内联模块保留独立、不会与上传文件名碰撞的虚拟身份。

作者内联 import map 支持 imports、scopes、精确映射、最长前缀、最长作用域、null 阻断和多份 map 的首次映射优先；必须放在模块/预载之前，外部 map、重复键、未审核域名、越界路径及未知字段明确拒绝。query 作用域也预编译对应变体，计算型参数/片段仍选择正确的作用域。计算型 import 只能从包内已构建 JS/MJS/JSON 清单及审核过的外部来源取得代码，不触发服务器依赖安装。

固定客户端装载器在 App sandbox 中查清单，不创建宿主文件读取、shell 或服务端代码执行入口。作者映射合并总量上限 2048 条/512 个 scope，派生原生映射上限 4096 条；大别名/前缀重复引用在完整 JSON 序列化之前计入预算，防止清单无限放大。缺失计算型模块、blocked 映射或未审核外部模块在客户端明确失败，不尝试其他来源。

JSON 文件经标准 JSON 校验后生成固定的 `JSON.parse` 客户端包装模块；静态导入只移除语法树确认的 `type: "json"`，动态导入也只接受该 JSON 类型属性，未知属性明确失败。这样不依赖所有 Host 浏览器都支持原生 JSON 模块，也不会把 `__proto__` 当作对象字面量 setter。其他 JS 动态 import options 交回浏览器，不由服务器求值。

## 版本和原始内容

新草稿默认 `html-assembly-5`，单独冻结派生 HTML、payload 摘要和装配版本。旧 html-assembly-1/2/3/4 继续走原逻辑；不因依赖升级重写原 ZIP 或改变旧已发布版本。新增 assembly-4 的数据库冻结读取及模块图原内容回归，以及含作者 map、参数入口和计算型 import 的正式上传/发布/预览/原字节下载回归。

仅为派生的 data URL 补充 CSP resourceDomains 中的 `data:`，不增加 connectDomains、外部 origin、任意 sandbox 权限或 Host 凭证。多个大模块入口先累计容量再生成最终字符串，避免最后 join 才发现容量超限。

## 实际证据

`test_app_module_graph.py` 13 项保留 v4 静态图证据；`test_app_module_v5.py` 21 项补齐计算表达式、作用域/query、作者映射、null/重复键/越界拒绝、内联身份、经典脚本 bare import、映射合并限额与清单防放大。正式管理 API 覆盖发布冻结、原字节下载和已发布 v4 模块图读取。完整后端 **698 passed，70 warnings**，包含固定 tree-sitter 超时属性弃用警告及既有配置警告；相关 Black/isort、Flake8（与 Black 兼容的 E501/W503/E203 忽略项）及 Node 语法检查通过，不隐藏警告冒充无问题。

在 Codex 内置浏览器的 `sandbox="allow-scripts"` 不透明 iframe 打开本机可信夹具，实际页面显示：

```text
computed=7; variants=1,2; same=true; scoped=2,2; prefix=1; asset=true; once=1; nested=1; denied=true; meta=true; cycle=5; chosen=1; queryScope=2,2; coercion=1; sandbox=true
```

这验证了计算型/嵌套导入、参数实例隔离、scopes/前缀、JSON、附件、只求值一次、未授权/缺失模块拒绝、query 作用域和无法读取父 DOM。此前 v4 对照页另确认共享模块只执行一次、相同字节的两个不同路径各执行一次。没有将这些结果泛化为所有浏览器、真实 MCP Apps Host 或 MCP 工具交互验收。临时 wheel 构建另确认固定 JS 装载器和新增 Python 模块都被包含，未发布制品。

## Prefab 和兼容边界

Prefab 继续使用固定官方 prefab-ui 0.20.2 的 bundled renderer 和公开纯数据构造接口。`test_app_prefab_renderer.py` 保证官方 HTML/摘要一致、state/表达式/onSuccess 原样保留、toolCall 只适配已审核绑定、未知别名/版本拒绝、CSP/权限不放宽、原始 JSON 不改且状态中的代码文本不在服务器求值。上传 Prefab 不执行作者 Python DSL；配置为上游 MCP 的程序仍在其明确配置的运行环境执行。

上传的是已构建浏览器模块，不是 npm 源码工程；JSX/未知语法、未知 import map 字段等明确校验失败。嵌套框架/对象、worker 或其他超出已审核 CSP/Host 能力的浏览器功能不通过扩大 sandbox 权限兼容。客户端需要实际支持原生模块/import map 和对应 Apps 能力；旧 MCP 客户端仍保留原普通结果，不能据协议声明推断其会渲染界面。

可信夹具仅监听本机，不连接用户数据库或 MCP。复现时在 `mcpcat/` 与隔离依赖环境运行：

```bash
PYTHONPATH=. python tests/browser/app_html_fixture.py --port 18763
```

打开 `http://127.0.0.1:18763/?case=modules-v5-sandbox` 观察结果；`case=modules` 保留 v4 对照，结束后停止测试进程。9.6 完成不替代 9.8/9.12/9.13 的完整 Apps 链路及全提案发布验收。
