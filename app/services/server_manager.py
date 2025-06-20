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
        
        # 新增：用于管理动态添加的服务器生命周期
        self.app_started = False  # 应用是否已启动
        self.running_contexts = {}  # 存储动态启动的服务器上下文
    
    def _update_server_status(self, server_name: str, status: str, error: Optional[str] = None):
        """
        统一的服务器状态更新方法
        
        Args:
            server_name: 服务器名称
            status: 新状态
            error: 错误信息（可选）
        """
        if server_name in self.server_info:
            self.server_info[server_name]['status'] = status
            if error:
                self.server_info[server_name]['error'] = error
            elif 'error' in self.server_info[server_name]:
                # 清除之前的错误信息
                del self.server_info[server_name]['error']
    
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
                self._update_server_status(key, 'failed', str(e))
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
    
    def mount_server(self, app: FastAPI, server_name: str) -> bool:
        """
        动态挂载单个服务器到运行中的FastAPI应用
        
        Args:
            app: FastAPI应用实例
            server_name: 服务器名称
            
        Returns:
            bool: 是否成功挂载
        """
        if server_name not in self.server_info:
            logger.error(f"服务器 {server_name} 不存在")
            return False
        
        try:
            # 查找该服务器的挂载配置
            server_mounts = [
                mount for mount in self.app_mount_list 
                if mount['path'] in [f'/mcp/{server_name}', f'/sse/{server_name}']
            ]
            
            if not server_mounts:
                logger.error(f"未找到服务器 {server_name} 的挂载配置")
                return False
            
            # 动态挂载
            for mount_config in server_mounts:
                path = mount_config['path']
                sub_app = mount_config['app']
                
                try:
                    print(f"动态挂载 {path} 到应用")
                    app.mount(path, sub_app)
                    logger.info(f"✓ 成功挂载 {path}")
                except Exception as mount_error:
                    # 如果挂载失败（可能是路径冲突），记录但继续
                    logger.warning(f"挂载 {path} 时出现警告: {mount_error}")
            
            # 更新服务器状态
            self._update_server_status(server_name, 'mounted')
            return True
            
        except Exception as e:
            logger.error(f"挂载服务器 {server_name} 失败: {e}")
            self._update_server_status(server_name, 'mount_failed', str(e))
            return False
    
    async def add_and_mount_server(self, app: FastAPI, key: str, value: Dict[str, Any]) -> bool:
        """
        添加并动态挂载MCP服务器到运行中的应用
        
        Args:
            app: FastAPI应用实例
            key: 服务器名称
            value: 服务器配置
            
        Returns:
            bool: 是否成功添加并挂载
        """
        # 检查服务器是否已存在
        if key in self.lifespan_tasks:
            logger.error(f"服务器 {key} 已存在")
            return False
        
        # 先添加服务器
        if not self.add_mcp_server(key, value):
            return False
        
        # 然后动态挂载
        if not self.mount_server(app, key):
            return False
        
        # 如果应用已经在运行，立即启动这个服务器的生命周期
        if self.app_started:
            try:
                # 获取生命周期任务
                task_lifespan = self.lifespan_tasks[key]
                ctx = task_lifespan(app)
                
                # 启动生命周期
                await ctx.__aenter__()
                self.running_contexts[key] = ctx
                
                print(f"✓ 动态服务器 {key} 生命周期启动成功")
                
                # 更新服务器状态
                self._update_server_status(key, 'running')
                
            except Exception as e:
                print(f"✗ 动态服务器 {key} 生命周期启动失败: {e}")
                logger.error(f"启动服务器 {key} 生命周期失败: {e}")
                
                # 清理已添加的服务器
                if key in self.lifespan_tasks:
                    del self.lifespan_tasks[key]
                self._update_server_status(key, 'failed', str(e))
                
                return False
        
        return True
    
    @asynccontextmanager
    async def create_unified_lifespan(self, app: FastAPI):
        """
        创建统一的生命周期管理器 - 与原有的 lifespan_manager 逻辑完全一致
        
        Args:
            app: FastAPI应用实例
        """
        print("应用启动中...")
        
        # 设置应用已启动状态
        self.app_started = True
        
        # 使用 AsyncExitStack 来正确管理所有的 lifespan 上下文 - 与原逻辑一致
        async with AsyncExitStack() as stack:
            # 启动所有任务 - 与原逻辑完全一致
            for task_name, task_lifespan in self.lifespan_tasks.items():
                try:
                    # 使用 enter_async_context 而不是手动调用 __aenter__ - 与原逻辑一致
                    await stack.enter_async_context(task_lifespan(app))
                    print(f"✓ 任务 {task_name} 启动成功")
                    
                    # 更新服务器状态
                    self._update_server_status(task_name, 'running')
                        
                except Exception as e:
                    print(f"✗ 任务 {task_name} 启动失败: {e}")
                    
                    # 更新服务器状态
                    self._update_server_status(task_name, 'failed', str(e))
            
            yield
            
            print("应用关闭中...")
            
            # 关闭动态启动的服务器
            for server_name, ctx in self.running_contexts.items():
                try:
                    await ctx.__aexit__(None, None, None)
                    print(f"✓ 动态服务器 {server_name} 关闭成功")
                except Exception as e:
                    print(f"✗ 动态服务器 {server_name} 关闭失败: {e}")
            
            # AsyncExitStack 会自动按相反顺序调用所有的 __aexit__ - 与原逻辑一致
            
            # 更新所有服务器状态为已停止
            for task_name in self.lifespan_tasks.keys():
                self._update_server_status(task_name, 'stopped')
            
            # 清理状态
            self.app_started = False
            self.running_contexts.clear()
    
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