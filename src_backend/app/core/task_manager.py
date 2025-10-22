"""
后台任务管理器
使用 asyncio 实现异步任务执行和 SSE 事件推送
"""

import asyncio
import uuid
from datetime import datetime
from typing import Dict, Callable, Any, Optional
from enum import Enum
import logging
import threading

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskInfo:
    """任务信息"""
    def __init__(self, task_id: str, task_type: str, metadata: Dict[str, Any]):
        self.task_id = task_id
        self.task_type = task_type
        self.status = TaskStatus.PENDING
        self.metadata = metadata
        self.result = None
        self.error = None
        self.created_at = datetime.utcnow()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None


class SSEManager:
    """SSE 事件管理器"""
    def __init__(self):
        # 存储所有活跃的 SSE 连接: user_id -> list of queues
        self._connections: Dict[int, list[asyncio.Queue]] = {}

    def add_connection(self, user_id: int) -> asyncio.Queue:
        """添加新的 SSE 连接"""
        queue = asyncio.Queue()
        if user_id not in self._connections:
            self._connections[user_id] = []
        self._connections[user_id].append(queue)
        logger.info(f"[SSE] 新连接建立，用户ID: {user_id}, 当前连接数: {len(self._connections[user_id])}")
        return queue

    def remove_connection(self, user_id: int, queue: asyncio.Queue):
        """移除 SSE 连接"""
        if user_id in self._connections:
            try:
                self._connections[user_id].remove(queue)
                logger.info(f"[SSE] 连接关闭，用户ID: {user_id}, 剩余连接数: {len(self._connections[user_id])}")
                if not self._connections[user_id]:
                    del self._connections[user_id]
            except ValueError:
                pass

    def send_event(self, user_id: int, event_type: str, data: Dict[str, Any]):
        """向指定用户的所有连接发送事件（非阻塞同步版本）"""
        if user_id not in self._connections:
            logger.warning(f"[SSE] 用户 {user_id} 没有活跃连接")
            return

        event = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.utcnow().isoformat()
        }

        # 向该用户的所有连接推送事件（非阻塞）
        dead_queues = []
        for queue in self._connections[user_id]:
            try:
                # 使用 put_nowait 避免阻塞
                queue.put_nowait(event)
                logger.info(f"[SSE] 事件已发送: {event_type}, 用户ID: {user_id}")
            except asyncio.QueueFull:
                logger.warning(f"[SSE] 队列已满，跳过事件: {event_type}, 用户ID: {user_id}")
            except Exception as e:
                logger.error(f"[SSE] 发送事件失败: {e}")
                dead_queues.append(queue)

        # 清理失败的队列
        for queue in dead_queues:
            self.remove_connection(user_id, queue)


class TaskManager:
    """任务管理器 - 单例模式"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._tasks: Dict[str, TaskInfo] = {}
        self._sse_manager = SSEManager()
        self._initialized = True
        logger.info("[TaskManager] 任务管理器初始化完成")

    def create_task(self, task_type: str, metadata: Dict[str, Any]) -> str:
        """创建新任务并返回任务ID"""
        task_id = str(uuid.uuid4())
        task_info = TaskInfo(task_id, task_type, metadata)
        self._tasks[task_id] = task_info
        logger.info(f"[Task] 创建任务: {task_id}, 类型: {task_type}")
        return task_id

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        """获取任务信息"""
        return self._tasks.get(task_id)

    async def execute_task(
        self,
        task_id: str,
        task_func: Callable,
        *args,
        **kwargs
    ):
        """执行后台任务"""
        task_info = self._tasks.get(task_id)
        if not task_info:
            logger.error(f"[Task] 任务不存在: {task_id}")
            return

        try:
            # 更新任务状态为处理中
            task_info.status = TaskStatus.PROCESSING
            task_info.started_at = datetime.utcnow()
            logger.info(f"[Task] 开始执行任务: {task_id}")

            # 发送开始事件（如果有 user_id）- 非阻塞
            user_id = task_info.metadata.get("user_id")
            if user_id:
                self._sse_manager.send_event(
                    user_id,
                    "task_started",
                    {
                        "task_id": task_id,
                        "task_type": task_info.task_type
                    }
                )

            # 执行任务函数
            if asyncio.iscoroutinefunction(task_func):
                result = await task_func(*args, **kwargs)
            else:
                # 在线程池中执行同步函数
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, task_func, *args, **kwargs)

            # 更新任务状态为完成
            task_info.status = TaskStatus.COMPLETED
            task_info.result = result
            task_info.completed_at = datetime.utcnow()

            processing_time = (task_info.completed_at - task_info.started_at).total_seconds()
            logger.info(f"[Task] 任务完成: {task_id}, 耗时: {processing_time:.2f}s")

            # 发送完成事件 - 非阻塞
            if user_id:
                self._sse_manager.send_event(
                    user_id,
                    "task_completed",
                    {
                        "task_id": task_id,
                        "task_type": task_info.task_type,
                        "result": result,
                        "processing_time": processing_time
                    }
                )

        except Exception as e:
            # 更新任务状态为失败
            task_info.status = TaskStatus.FAILED
            task_info.error = str(e)
            task_info.completed_at = datetime.utcnow()
            logger.error(f"[Task] 任务失败: {task_id}, 错误: {str(e)}", exc_info=True)

            # 发送失败事件 - 非阻塞
            user_id = task_info.metadata.get("user_id")
            if user_id:
                self._sse_manager.send_event(
                    user_id,
                    "task_failed",
                    {
                        "task_id": task_id,
                        "task_type": task_info.task_type,
                        "error": str(e)
                    }
                )

    def submit_task(
        self,
        task_type: str,
        task_func: Callable,
        metadata: Dict[str, Any],
        *args,
        **kwargs
    ) -> str:
        """提交后台任务（真正非阻塞 - 使用独立线程）"""
        task_id = self.create_task(task_type, metadata)

        # 在独立线程中运行任务，避免阻塞HTTP响应
        def run_task_in_thread():
            """在独立线程中运行异步任务"""
            # 创建新的事件循环
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # 运行任务直到完成
                loop.run_until_complete(
                    self.execute_task(task_id, task_func, *args, **kwargs)
                )
            finally:
                loop.close()

        # 启动后台线程
        thread = threading.Thread(target=run_task_in_thread, daemon=True)
        thread.start()

        return task_id

    @property
    def sse_manager(self) -> SSEManager:
        """获取 SSE 管理器"""
        return self._sse_manager

    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """清理旧任务（可选的维护功能）"""
        now = datetime.utcnow()
        old_tasks = []

        for task_id, task_info in self._tasks.items():
            if task_info.completed_at:
                age = (now - task_info.completed_at).total_seconds() / 3600
                if age > max_age_hours:
                    old_tasks.append(task_id)

        for task_id in old_tasks:
            del self._tasks[task_id]

        if old_tasks:
            logger.info(f"[TaskManager] 清理了 {len(old_tasks)} 个旧任务")


# 全局单例
task_manager = TaskManager()
