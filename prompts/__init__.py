"""提示词模块"""

from .main_inject import inject_webapp_status
from .webdev_system import build_webdev_messages, build_webdev_system_prompt

__all__ = [
    "build_webdev_messages",
    "build_webdev_system_prompt",
    "inject_webapp_status",
]

