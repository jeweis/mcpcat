"""仅将固定的启动错误解释传给用户，原始 stderr 仍留在管理员日志。"""

MESSAGES = {
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
