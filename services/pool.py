"""Agent 池管理

使用框架 AgentPool 管理 WebDevAgent。
"""

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .task_tracer import TaskTracer

from nekro_agent.core import logger

from ..agent_core import AgentPool, MessageBus, StatusInjector
from ..models import WebDevAgent
from ..plugin import config, plugin

# 初始化框架组件
pool: AgentPool[WebDevAgent] = AgentPool(
    plugin=plugin,
    agent_class=WebDevAgent,
    store_key="webdev",
    max_concurrent=config.MAX_CONCURRENT_AGENTS_PER_CHAT,
    id_prefix="Web_",
)

bus: MessageBus[WebDevAgent] = MessageBus(pool)

injector: StatusInjector[WebDevAgent] = StatusInjector(
    pool=pool,
    title="🌐 网页开发助手",
    formatter=lambda a: (
        f"- **[{a.agent_id}]** {a.status.value} (⭐{a.difficulty}) "
        f"| {a.current_step or a.task[:30]}..."
    ),
)


# ==================== 便捷函数 ====================


async def create_agent(
    chat_key: str,
    requirement: str,
    difficulty: int = 5,
    tracer: Optional["TaskTracer"] = None,
) -> WebDevAgent:
    """创建 Agent"""
    agent = await pool.create(
        chat_key=chat_key,
        task=requirement,
        difficulty=difficulty,
    )

    if tracer:
        tracer.log_event("AGENT_CREATE", agent.agent_id, f"创建 Agent: {agent.agent_id} @ {chat_key}")
    else:
        logger.info(f"[WebDev] 🌟 创建 Agent: {agent.agent_id} @ {chat_key}")
    return agent


async def get_agent(agent_id: str, chat_key: str) -> WebDevAgent | None:
    """获取 Agent"""
    return await pool.get(chat_key, agent_id)


async def update_agent(agent: WebDevAgent) -> None:
    """更新 Agent"""
    await pool.update(agent)


async def get_active_agents(chat_key: str) -> list[WebDevAgent]:
    """获取活跃 Agent 列表"""
    return await pool.get_active(chat_key)


async def generate_status(chat_key: str) -> str:
    """生成状态注入文本"""
    return await injector.generate(chat_key)


async def send_to_main(
    chat_key: str,
    agent_id: str,
    content: str,
    trigger: bool = False,
    tracer: Optional["TaskTracer"] = None,
) -> bool:
    """子 Agent 发消息给主 Agent"""
    if tracer:
        tracer.log_event("MSG_SUB_TO_MAIN", agent_id, f"{agent_id} -> 主 Agent: {content[:100]}... (trigger={trigger})")
    else:
        logger.info(
            f"[WebDev] ⬆️ {agent_id} -> 主 Agent: {content[:50]}... (trigger={trigger})",
        )
    return await bus.sub_to_main(chat_key, agent_id, content, trigger=trigger)


async def send_to_sub(
    chat_key: str,
    agent_id: str,
    content: str,
    msg_type: str = "feedback",
    tracer: Optional["TaskTracer"] = None,
) -> bool:
    """主 Agent 发消息给子 Agent"""
    if tracer:
        tracer.log_event("MSG_MAIN_TO_SUB", agent_id, f"主 Agent -> {agent_id}: {content[:100]}... (type={msg_type})")
    else:
        logger.info(
            f"[WebDev] ⬇️ 主 Agent -> {agent_id}: {content[:50]}... (type={msg_type})",
        )
    return await bus.main_to_sub(chat_key, agent_id, content, msg_type)
