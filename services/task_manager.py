"""多任务管理器

管理 WebApp 开发任务的生命周期：
- 创建/追加/归档任务
- 任务状态跟踪
- 并行任务限制
"""

import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from nekro_agent.core.logger import logger

from ..plugin import config

TaskStatus = Literal["pending", "running", "success", "failed", "archived"]


@dataclass
class WebAppTask:
    """WebApp 任务"""

    task_id: str
    chat_key: str
    description: str
    status: TaskStatus = "pending"
    requirements: List[str] = field(default_factory=list)  # 需求历史（支持追加）
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    url: Optional[str] = None
    error: Optional[str] = None
    progress: int = 0

    def add_requirement(self, requirement: str) -> None:
        """追加需求"""
        self.requirements.append(requirement)
        self.updated_at = time.time()

    def get_full_requirement(self) -> str:
        """获取完整需求（所有追加）"""
        return "\n\n---\n\n".join(self.requirements)


class TaskManager:
    """任务管理器

    管理每个 chat_key 下的多个任务。
    """

    def __init__(self) -> None:
        # chat_key -> {task_id -> WebAppTask}
        self._tasks: Dict[str, Dict[str, WebAppTask]] = {}

    def create_task(self, chat_key: str, requirement: str) -> WebAppTask:
        """创建新任务"""
        # 检查并行任务数限制
        active_count = self._count_active_tasks(chat_key)
        max_tasks = getattr(config, "MAX_CONCURRENT_TASKS", 3)
        if active_count >= max_tasks:
            raise ValueError(
                f"已达最大并行任务数 ({max_tasks})，请等待任务完成或归档旧任务",
            )

        # 生成唯一任务 ID (时间戳 + 随机后缀)
        # 生成唯一任务 ID
        # 规则：T + 4位随机数字，重复则增长位宽，最大8位
        existing_ids = set(self._tasks.get(chat_key, {}).keys())
        task_id = ""
        
        # 尝试长度从 4 到 8
        for length in range(4, 9):
            # 每个长度尝试 20 次
            for _ in range(20):
                suffix = "".join(str(random.randint(0, 9)) for _ in range(length))
                candidate = f"T{suffix}"
                if candidate not in existing_ids:
                    task_id = candidate
                    break
            if task_id:
                break
        else:
            # 极端保底：使用时间戳
            task_id = f"T{int(time.time() * 1000)}"

        task = WebAppTask(
            task_id=task_id,
            chat_key=chat_key,
            description=requirement.strip()[:100],
            requirements=[requirement.strip()],
        )

        if chat_key not in self._tasks:
            self._tasks[chat_key] = {}
        self._tasks[chat_key][task_id] = task

        logger.info(f"[TaskManager] 创建任务 {task_id}: {task.description[:50]}...")
        return task

    def get_task(self, chat_key: str, task_id: str) -> Optional[WebAppTask]:
        """获取任务"""
        return self._tasks.get(chat_key, {}).get(task_id)

    def append_requirement(self, chat_key: str, task_id: str, requirement: str) -> bool:
        """追加需求"""
        task = self.get_task(chat_key, task_id)
        if not task:
            return False

        task.add_requirement(requirement.strip())
        # 如果任务失败，重置为 pending 以允许重试
        if task.status == "failed":
            task.status = "pending"
            task.error = None
        logger.info(f"[TaskManager] 任务 {task_id} 追加需求")
        return True

    def update_status(
        self,
        chat_key: str,
        task_id: str,
        status: TaskStatus,
        url: Optional[str] = None,
        error: Optional[str] = None,
        progress: int = 0,
    ) -> bool:
        """更新任务状态"""
        task = self.get_task(chat_key, task_id)
        if not task:
            return False

        task.status = status
        task.updated_at = time.time()
        task.progress = progress
        if url:
            task.url = url
        if error:
            task.error = error
        return True

    def archive_task(self, chat_key: str, task_id: str) -> bool:
        """归档任务"""
        task = self.get_task(chat_key, task_id)
        if not task:
            return False

        task.status = "archived"
        task.updated_at = time.time()
        logger.info(f"[TaskManager] 任务 {task_id} 已归档")
        return True

    def list_active_tasks(self, chat_key: str) -> List[WebAppTask]:
        """列出活跃任务（包含 pending, running, success, failed）"""
        tasks = self._tasks.get(chat_key, {})
        return [t for t in tasks.values() if t.status != "archived"]

    def list_all_tasks(self, chat_key: str) -> List[WebAppTask]:
        """列出所有任务"""
        return list(self._tasks.get(chat_key, {}).values())

    def _count_active_tasks(self, chat_key: str) -> int:
        """统计活跃任务数（pending + running）"""
        tasks = self._tasks.get(chat_key, {})
        return sum(1 for t in tasks.values() if t.status in ("pending", "running"))

    def get_pending_task(self, chat_key: str) -> Optional[WebAppTask]:
        """获取下一个待执行任务"""
        for task in self.list_active_tasks(chat_key):
            if task.status == "pending":
                return task
        return None


# 全局单例
task_manager = TaskManager()
