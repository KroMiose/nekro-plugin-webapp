"""服务模块

包含 WebDev Agent 的核心服务：池管理、任务运行、部署。
"""

from .agent_runner import (
    cancel_agent_task,
    confirm_agent_task,
    start_agent_task,
    stop_agent_task,
    stop_all_tasks,
    wake_up_agent,
)
from .deploy import deploy_html_to_worker
from .pool import (
    bus,
    create_agent,
    generate_status,
    get_active_agents,
    get_agent,
    injector,
    pool,
    send_to_main,
    send_to_sub,
    update_agent,
)
from .stream_parser import ParseResult, StreamParser

__all__ = [
    "ParseResult",
    "StreamParser",
    "bus",
    "cancel_agent_task",
    "confirm_agent_task",
    "create_agent",
    "deploy_html_to_worker",
    "generate_status",
    "get_active_agents",
    "get_agent",
    "injector",
    "pool",
    "send_to_main",
    "send_to_sub",
    "start_agent_task",
    "stop_agent_task",
    "stop_all_tasks",
    "update_agent",
    "wake_up_agent",
]
