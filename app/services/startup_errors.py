"""仅将固定的启动错误解释传给用户，原始 stderr 仍留在管理员日志。"""

MESSAGES = {
    "RUNTIME_HTTP": "上游 HTTP 返回错误状态，请查看诊断日志中的 HTTP 状态码和端点配置",
    "RUNTIME_DEPENDENCY": "上游依赖或运行时不兼容，请查看当前连接尝试的 stderr 与依赖快照",
    "RUNTIME_CACHE": "上游安装或缓存失败，可在运行管理创建新环境修订后重试连接",
    "RUNTIME_AUTHENTICATION": "上游认证或权限失败，请检查该服务的凭证和权限",
    "RUNTIME_TLS": "上游 TLS/证书校验失败，请检查容器信任链和代理配置",
    "RUNTIME_DNS": "上游 DNS 解析失败，请检查容器 DNS、代理或包源连通性",
    "RUNTIME_NETWORK": "上游网络连接失败，请检查目标端口及代理",
    "RUNTIME_PROTOCOL": "上游不支持可共同使用的 MCP 协议版本，请检查运行管理中的协商记录",
    "RUNTIME_PROCESS_EXIT": "上游进程异常退出，请查看该尝试的真实退出码及 stderr",
    "RUNTIME_KILLED": "上游进程被终止，请检查容器内存限制和外部终止记录",
    "RUNTIME_EXECUTABLE": "上游可执行文件不存在或无法运行，请检查运行环境",
    "RUNTIME_CONNECTION_CLOSED": "上游在初始化期间断开连接，自动恢复未成功，请查看运行管理中的连接尝试",
    "PREPARE_FAILED": "mcpdoc 启动环境准备失败，请在诊断日志查看 prepare 阶段的原始错误",
    "DEPENDENCY_OVERRIDE": "mcpdoc 依赖不兼容；检测到自定义依赖约束，未自动覆盖，请检查诊断日志",
    "MCPDOC_DEPENDENCY": "mcpdoc 依赖兼容环境准备失败，请检查包源连通性及 prepare 阶段日志",
    "EXECUTABLE_MISSING": "上游启动器不存在或无法执行，请检查运行环境及 prepare 阶段日志",
}


def startup_error_message(error: BaseException) -> str | None:
    """SDK 可能包装异常为 RPC 文本，只匹配我们定义的固定标识。"""
    pending = [error]
    seen = set()
    while pending and len(seen) < 20:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        for code, message in MESSAGES.items():
            if f"[MCPCAT_STARTUP:{code}]" in str(item):
                return message
        pending.extend(
            child for child in (item.__cause__, item.__context__) if child is not None
        )
        pending.extend(getattr(item, "exceptions", ()))
    return None
