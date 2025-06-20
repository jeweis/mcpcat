"""服务器管理器 - 封装MCP服务器管理逻辑"""

import logging
from typing import Dict, List, Any, Optional, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from fastapi import FastAPI

from app.services.config_service import ConfigService
from app.services.mcp_factory import MCPServerFactory

logger = logging.getLogger(__name__)


class MCPServerManager:
    """MCP服务器管理器 - 封装现有的服务器管理逻辑"""
    
    def __init__(self):
        # 与原有代码保持一致的数据结构
        self.lifespan_tasks: Dict[str, Callable] = {}  # 对应原有的 lifespan_tasks
        self.app_mount_list: List[Dict[str, Any]] = []  # 对应原有的 app_mount_list
        self.server_info: Dict[str, Dict[str, Any]] = {}  # 额外的服务器信息
    
    def load_servers_from_config(self) -> None:
        """
        从配置文件加载所有MCP服务器 - 保持与原有逻辑完全一致
        """
        # 使用 ConfigService.load_config() 保持向后兼容
        mcp_server_list = ConfigService.load_config()
        
        print("Loading MCP server list...")
        print("MCP server list loaded.")
        print(mcp_server_list)
        
        # 遍历配置并添加服务器 - 与原逻辑一致
        for key, value in mcp_server_list.items():
            self.add_mcp_server(key, value)
    
    def add_mcp_server(self, key: str, value: Dict[str, Any]) -> bool:
        """
        添加MCP服务器 - 与原有的 add_mcp_server 函数逻辑完全一致
        
        Args:
            key: 服务器名称
            value: 服务器配置
            
        Returns:
            bool: 是否成功添加
        """
        try:
            # 使用工厂创建MCP服务器
            mcp = MCPServerFactory.create_server(key, value)
            
            if mcp is None:
                return False
            
            # 创建应用 - 与原逻辑完全一致
            mcp_app = mcp.http_app(path='/')
            sse_app = mcp.http_app(path="/", transport="sse")
            
            # 添加到挂载列表 - 与原逻辑完全一致
            self.app_mount_list.append({"path": f'/mcp/{key}', "app": mcp_app})
            self.app_mount_list.append({"path": f'/sse/{key}', "app": sse_app})
            
            # 添加到生命周期任务 - 与原逻辑完全一致
            self.lifespan_tasks[key] = mcp_app.lifespan
            
            # 存储服务器信息（新增，用于监控）
            self.server_info[key] = {
                'config': value,
                'mcp': mcp,
                'mcp_app': mcp_app,
                'sse_app': sse_app,
                'status': 'loaded'
            }
            
            logger.info(f"✓ MCP服务器 {key} 配置成功")
            return True
            
        except Exception as e:
            logger.error(f"❌ 创建MCP服务器 {key} 失败: {e}")
            if key in self.server_info:
                self.server_info[key]['status'] = 'failed'
                self.server_info[key]['error'] = str(e)
            return False
    
    def mount_all_servers(self, app: FastAPI) -> None:
        """
        将所有服务器挂载到FastAPI应用 - 与原有逻辑完全一致
        
        Args:
            app: FastAPI应用实例
        """
        # 遍历app_mount_list - 与原逻辑完全一致
        for app_mount in self.app_mount_list:
            print(f"Mounting {app_mount['path']} with {app_mount['app']}")
            app.mount(app_mount['path'], app_mount['app'])
    
    @asynccontextmanager
    async def create_unified_lifespan(self, app: FastAPI):
        """
        创建统一的生命周期管理器 - 与原有的 lifespan_manager 逻辑完全一致
        
        Args:
            app: FastAPI应用实例
        """
        print("应用启动中...")
        
        # 使用 AsyncExitStack 来正确管理所有的 lifespan 上下文 - 与原逻辑一致
        async with AsyncExitStack() as stack:
            # 启动所有任务 - 与原逻辑完全一致
            for task_name, task_lifespan in self.lifespan_tasks.items():
                try:
                    # 使用 enter_async_context 而不是手动调用 __aenter__ - 与原逻辑一致
                    await stack.enter_async_context(task_lifespan(app))
                    print(f"✓ 任务 {task_name} 启动成功")
                    
                    # 更新服务器状态
                    if task_name in self.server_info:
                        self.server_info[task_name]['status'] = 'running'
                        
                except Exception as e:
                    print(f"✗ 任务 {task_name} 启动失败: {e}")
                    
                    # 更新服务器状态
                    if task_name in self.server_info:
                        self.server_info[task_name]['status'] = 'failed'
                        self.server_info[task_name]['error'] = str(e)
            
            yield
            
            print("应用关闭中...")
            # AsyncExitStack 会自动按相反顺序调用所有的 __aexit__ - 与原逻辑一致
            
            # 更新所有服务器状态为已停止
            for task_name in self.lifespan_tasks.keys():
                if task_name in self.server_info:
                    self.server_info[task_name]['status'] = 'stopped'
    
    def get_server_status(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有服务器状态 - 新增的监控功能
        
        Returns:
            Dict[str, Dict[str, Any]]: 服务器状态信息
        """
        return {
            name: {
                'status': info.get('status', 'unknown'),
                'type': info.get('config', {}).get('type', 'unknown'),
                'error': info.get('error'),
                'mcp_endpoint': f"/mcp/{name}",
                'sse_endpoint': f"/sse/{name}"
            }
            for name, info in self.server_info.items()
        }
    
    def get_mount_list(self) -> List[Dict[str, Any]]:
        """
        获取挂载列表 - 向后兼容
        
        Returns:
            List[Dict[str, Any]]: 挂载列表
        """
        return self.app_mount_list.copy()
    
    def get_lifespan_tasks(self) -> Dict[str, Callable]:
        """
        获取生命周期任务 - 向后兼容
        
        Returns:
            Dict[str, Callable]: 生命周期任务字典
        """
        return self.lifespan_tasks.copy() 