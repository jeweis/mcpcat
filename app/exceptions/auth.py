"""认证相关异常"""

from fastapi import HTTPException, status


class AuthenticationError(HTTPException):
    """认证失败异常"""
    
    def __init__(self, detail: str = "Authentication required", auth_header_name: str = "Mcpcat-Key"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": auth_header_name}
        )


class PermissionDeniedError(HTTPException):
    """权限不足异常"""
    
    def __init__(self, detail: str = "Permission denied"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail
        )


class InvalidAPIKeyError(AuthenticationError):
    """无效API Key异常"""
    
    def __init__(self, auth_header_name: str = "Mcpcat-Key"):
        super().__init__(detail="Invalid API Key", auth_header_name=auth_header_name)


class ExpiredAPIKeyError(AuthenticationError):
    """API Key过期异常"""
    
    def __init__(self, auth_header_name: str = "Mcpcat-Key"):
        super().__init__(detail="API Key has expired", auth_header_name=auth_header_name)


class DisabledAPIKeyError(AuthenticationError):
    """API Key已禁用异常"""
    
    def __init__(self, auth_header_name: str = "Mcpcat-Key"):
        super().__init__(detail="API Key is disabled", auth_header_name=auth_header_name)