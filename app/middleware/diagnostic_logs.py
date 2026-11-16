"""请求范围诊断上下文；不读取请求正文或记录认证头。"""

from starlette.types import ASGIApp, Receive, Scope, Send

from app.services.diagnostic_logs import current_diagnostics


class DiagnosticContextMiddleware:
    """让异步请求与其子任务使用所属应用的诊断缓冲区。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """仅记录未捕获的服务端异常；交由原处理链返回错误。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        store = scope["app"].state.diagnostic_logs
        token = current_diagnostics.set(store)
        try:
            await self.app(scope, receive, send)
        except Exception as error:
            store.record(
                "未处理的 HTTP 请求异常", operation=scope["method"], error=error
            )
            raise
        finally:
            current_diagnostics.reset(token)
