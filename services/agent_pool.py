"""Agent 池管理服务

负责 Agent 的创建、查询、状态管理和并发控制。
所有 Agent 按会话隔离，会话之间互不影响。
"""

import os
import time
from typing import List, Optional, Tuple

from nekro_agent.api.core import logger

from ..models import (
    AgentMessage,
    AgentStatus,
    ChatAgentRegistry,
    MessageType,
    WebDevAgent,
)
from ..plugin import config, store

# ==================== 数据持久化 ====================


async def load_chat_registry(chat_key: str) -> ChatAgentRegistry:
    """加载会话级 Agent 注册表"""
    data = await store.get(chat_key=chat_key, store_key="webapp_agents")
    if data:
        return ChatAgentRegistry.model_validate_json(data)
    return ChatAgentRegistry()


async def save_chat_registry(chat_key: str, registry: ChatAgentRegistry) -> None:
    """保存会话级 Agent 注册表"""
    await store.set(
        chat_key=chat_key,
        store_key="webapp_agents",
        value=registry.model_dump_json(),
    )


# ==================== Agent CRUD ====================


def generate_agent_id() -> str:
    """生成唯一 Agent ID"""
    suffix = os.urandom(2).hex()
    return f"WEB-{suffix}"


async def create_agent(
    chat_key: str,
    requirement: str,
    difficulty: int = 5,
) -> Tuple[Optional[WebDevAgent], Optional[str]]:
    """创建新的 Agent

    Args:
        chat_key: 所属会话
        requirement: 任务需求
        difficulty: 任务难度评分 (1-10)

    Returns:
        Tuple[WebDevAgent, Optional[str]]: (Agent 实例, 错误信息)
    """
    # 先自动归档超时的 Agent
    archived = await auto_archive_expired_agents(chat_key)
    if archived:
        logger.info(f"创建前自动归档了 {len(archived)} 个超时 Agent")

    registry = await load_chat_registry(chat_key)

    # 检查并发限制 - 会话级（只计算正在工作的 Agent）
    working_count = len([a for a in registry.active_agents.values() if a.is_working()])
    if working_count >= config.MAX_CONCURRENT_AGENTS_PER_CHAT:
        # 尝试强制归档最早的已确认 Agent
        forced_id = await force_archive_oldest_confirmed(chat_key)
        if forced_id:
            logger.info(f"达到并发上限，强制归档 Agent: {forced_id}")
            registry = await load_chat_registry(chat_key)
            working_count = len(
                [a for a in registry.active_agents.values() if a.is_working()],
            )

    # 再次检查
    if working_count >= config.MAX_CONCURRENT_AGENTS_PER_CHAT:
        return (
            None,
            f"当前会话已有 {working_count} 个正在工作的 Agent，达到上限 {config.MAX_CONCURRENT_AGENTS_PER_CHAT}",
        )

    # 生成 Agent ID
    agent_id = generate_agent_id()

    # 创建 Agent
    agent = WebDevAgent.create(agent_id, chat_key, requirement, difficulty)

    # 添加初始消息
    agent.add_message(
        msg_type=MessageType.INSTRUCTION,
        sender="main",
        content=requirement,
    )

    # 保存到会话注册表
    registry.add_agent(agent)
    await save_chat_registry(chat_key, registry)

    logger.info(
        f"创建 WebDev Agent: {agent_id}, chat_key={chat_key}, 难度={difficulty}",
    )
    return agent, None


async def get_agent(
    agent_id: str,
    chat_key: str,
    update_access: bool = True,
) -> Optional[WebDevAgent]:
    """获取 Agent（从会话注册表）

    Args:
        agent_id: Agent ID
        chat_key: 会话 key
        update_access: 是否更新访问时间（用于自动归档判断）

    Returns:
        Agent 实例或 None
    """
    registry = await load_chat_registry(chat_key)
    agent = registry.get_agent(agent_id)
    if agent and update_access:
        agent.touch()
        await save_chat_registry(chat_key, registry)
    return agent


async def get_agent_by_id(agent_id: str, chat_key: str) -> Optional[WebDevAgent]:
    """获取 Agent（别名）"""
    return await get_agent(agent_id, chat_key)


async def get_chat_registry(chat_key: str) -> ChatAgentRegistry:
    """获取会话 Agent 注册表（别名）"""
    return await load_chat_registry(chat_key)


async def update_agent(agent: WebDevAgent) -> None:
    """更新 Agent 状态"""
    registry = await load_chat_registry(agent.chat_key)

    # 更新会话注册表
    if agent.agent_id in registry.active_agents:
        registry.active_agents[agent.agent_id] = agent

    await save_chat_registry(agent.chat_key, registry)


async def confirm_agent(agent_id: str, chat_key: str) -> Optional[WebDevAgent]:
    """软确认 Agent 完成（不立即归档）

    用户确认任务完成后，Agent 进入 WAITING_CONFIRM 状态，
    但仍保留在活跃列表中，直到自动归档或被强制归档。
    """
    registry = await load_chat_registry(chat_key)
    agent = registry.get_agent(agent_id)

    if not agent:
        return None

    # 标记为已确认
    agent.confirm()
    agent.progress_percent = 100
    agent.current_step = "已确认完成"
    await save_chat_registry(chat_key, registry)

    logger.info(f"确认 WebDev Agent 完成: {agent_id}")
    return agent


async def archive_agent(agent_id: str, chat_key: str) -> Optional[WebDevAgent]:
    """归档 Agent（将其从活跃列表移出）"""
    registry = await load_chat_registry(chat_key)
    agent = registry.get_agent(agent_id)

    if not agent:
        return None

    # 更新状态
    agent.update_status(AgentStatus.COMPLETED)
    agent.complete_time = int(time.time())

    # 从会话注册表中归档
    registry.archive_agent(agent_id, config.MAX_COMPLETED_HISTORY)
    await save_chat_registry(chat_key, registry)

    logger.info(f"归档 WebDev Agent: {agent_id}")
    return agent


async def auto_archive_expired_agents(chat_key: str) -> List[str]:
    """自动归档超时的已确认 Agent，并标记超时的等待中 Agent 为失败

    Returns:
        已归档的 Agent ID 列表
    """
    registry = await load_chat_registry(chat_key)
    archived_ids: List[str] = []
    failed_ids: List[str] = []

    # 1. 归档超时的已确认 Agent
    agents_to_archive = [
        agent_id
        for agent_id, agent in registry.active_agents.items()
        if agent.should_auto_archive(config.AUTO_ARCHIVE_MINUTES)
    ]

    for agent_id in agents_to_archive:
        agent = registry.active_agents.get(agent_id)
        if agent:
            agent.update_status(AgentStatus.COMPLETED)
            agent.complete_time = int(time.time())
            registry.archive_agent(agent_id, config.MAX_COMPLETED_HISTORY)
            archived_ids.append(agent_id)
            logger.info(f"自动归档超时 Agent: {agent_id}")

    # 2. 标记超时的等待反馈 Agent 为失败
    agents_to_fail = [
        agent_id
        for agent_id, agent in registry.active_agents.items()
        if agent.is_timeout(config.AGENT_TIMEOUT_MINUTES)
    ]

    for agent_id in agents_to_fail:
        agent = registry.active_agents.get(agent_id)
        if agent:
            agent.update_status(AgentStatus.FAILED)
            agent.error_message = f"等待反馈超时 ({config.AGENT_TIMEOUT_MINUTES} 分钟)"
            registry.remove_agent(agent_id)
            failed_ids.append(agent_id)
            logger.warning(f"Agent {agent_id} 等待反馈超时，标记为失败")

    if archived_ids or failed_ids:
        await save_chat_registry(chat_key, registry)

    return archived_ids


async def get_archived_agents_count(chat_key: str) -> int:
    """获取已归档 Agent 数量"""
    registry = await load_chat_registry(chat_key)
    return len(registry.completed_agents)


async def force_archive_oldest_confirmed(chat_key: str) -> Optional[str]:
    """强制归档最早的已确认 Agent（当达到并发上限时调用）

    Returns:
        被归档的 Agent ID，如果没有可归档的则返回 None
    """
    registry = await load_chat_registry(chat_key)

    # 查找所有 WAITING_CONFIRM 状态的 Agent，按确认时间排序
    confirmed_agents = [
        (agent_id, agent)
        for agent_id, agent in registry.active_agents.items()
        if agent.status == AgentStatus.WAITING_CONFIRM
    ]

    if not confirmed_agents:
        return None

    # 按确认时间排序，取最早的
    confirmed_agents.sort(key=lambda x: x[1].confirmed_time or 0)
    oldest_id, oldest_agent = confirmed_agents[0]

    # 归档
    oldest_agent.update_status(AgentStatus.COMPLETED)
    oldest_agent.complete_time = int(time.time())
    registry.archive_agent(oldest_id, config.MAX_COMPLETED_HISTORY)
    await save_chat_registry(chat_key, registry)

    logger.info(f"强制归档最早的已确认 Agent: {oldest_id}")
    return oldest_id


async def cancel_agent(
    agent_id: str,
    chat_key: str,
    reason: str = "",
) -> Optional[WebDevAgent]:
    """取消 Agent"""
    registry = await load_chat_registry(chat_key)
    agent = registry.get_agent(agent_id)

    if not agent:
        return None

    if not agent.is_active():
        logger.warning(f"Agent {agent_id} 已不在活跃状态，无法取消")
        return None

    # 更新状态
    agent.update_status(AgentStatus.CANCELLED)
    if reason:
        agent.error_message = f"已取消: {reason}"

    # 从会话注册表中移除（不归档）
    registry.remove_agent(agent_id)
    await save_chat_registry(chat_key, registry)

    logger.info(f"取消 WebDev Agent: {agent_id}, 原因: {reason}")
    return agent


async def fail_agent(
    agent_id: str,
    chat_key: str,
    error_message: str,
) -> Optional[WebDevAgent]:
    """标记 Agent 为失败状态"""
    registry = await load_chat_registry(chat_key)
    agent = registry.get_agent(agent_id)

    if not agent:
        return None

    # 更新状态
    agent.update_status(AgentStatus.FAILED)
    agent.error_message = error_message

    # 从会话注册表中移除
    registry.remove_agent(agent_id)
    await save_chat_registry(chat_key, registry)

    logger.error(f"WebDev Agent 失败: {agent_id}, 错误: {error_message}")
    return agent


async def add_message_to_agent(
    agent_id: str,
    chat_key: str,
    msg_type: MessageType,
    sender: str,
    content: str,
) -> Optional[AgentMessage]:
    """向 Agent 添加消息"""
    agent = await get_agent(agent_id, chat_key)
    if not agent:
        return None

    if sender not in ["main", "webdev"]:
        raise ValueError(f"Invalid sender: {sender}")

    msg = agent.add_message(msg_type, sender, content)  # type: ignore
    await update_agent(agent)
    return msg


async def update_agent_progress(
    agent_id: str,
    chat_key: str,
    percent: int,
    step: str,
) -> Optional[WebDevAgent]:
    """更新 Agent 进度"""
    agent = await get_agent(agent_id, chat_key)
    if not agent:
        return None

    agent.update_progress(percent, step)
    await update_agent(agent)
    return agent


async def update_agent_status(
    agent_id: str,
    chat_key: str,
    status: AgentStatus,
) -> Optional[WebDevAgent]:
    """更新 Agent 状态"""
    agent = await get_agent(agent_id, chat_key)
    if not agent:
        return None

    agent.update_status(status)
    await update_agent(agent)
    return agent


async def update_agent_html(
    agent_id: str,
    chat_key: str,
    html_content: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
) -> Optional[WebDevAgent]:
    """更新 Agent 的 HTML 内容"""
    agent = await get_agent(agent_id, chat_key)
    if not agent:
        return None

    agent.current_html = html_content
    if title:
        agent.page_title = title
    if description:
        agent.page_description = description

    await update_agent(agent)
    return agent


async def update_agent_deployed_url(
    agent_id: str,
    chat_key: str,
    url: str,
) -> Optional[WebDevAgent]:
    """更新 Agent 的部署 URL"""
    agent = await get_agent(agent_id, chat_key)
    if not agent:
        return None

    agent.deployed_url = url
    await update_agent(agent)
    return agent


async def get_active_agents_for_chat(chat_key: str) -> List[WebDevAgent]:
    """获取会话中所有活跃的 Agent"""
    registry = await load_chat_registry(chat_key)
    return [a for a in registry.active_agents.values() if a.is_active()]


# ==================== 模板变量管理 ====================


async def set_agent_template_var(
    agent_id: str,
    chat_key: str,
    key: str,
    value: str,
) -> bool:
    """设置 Agent 的模板变量

    Args:
        agent_id: Agent ID
        chat_key: 会话 key
        key: 变量名
        value: 变量值

    Returns:
        是否成功
    """
    agent = await get_agent(agent_id, chat_key, update_access=False)
    if not agent:
        return False

    agent.set_template_var(key, value)
    await update_agent(agent)

    logger.info(f"设置 Agent {agent_id} 模板变量: {key} ({len(value)} 字符)")
    return True


async def delete_agent_template_var(
    agent_id: str,
    chat_key: str,
    key: str,
) -> bool:
    """删除 Agent 的模板变量

    Args:
        agent_id: Agent ID
        chat_key: 会话 key
        key: 变量名

    Returns:
        是否成功
    """
    agent = await get_agent(agent_id, chat_key, update_access=False)
    if not agent:
        return False

    if not agent.delete_template_var(key):
        return False

    await update_agent(agent)

    logger.info(f"删除 Agent {agent_id} 模板变量: {key}")
    return True


async def get_agent_template_vars(
    agent_id: str,
    chat_key: str,
) -> dict[str, str]:
    """获取 Agent 的所有模板变量

    Args:
        agent_id: Agent ID
        chat_key: 会话 key

    Returns:
        模板变量字典
    """
    agent = await get_agent(agent_id, chat_key, update_access=False)
    if not agent:
        return {}
    return agent.template_vars.copy()


async def clean_completed_agents(chat_key: str) -> int:
    """清理已完成/失败/取消的 Agent

    Args:
        chat_key: 会话 key

    Returns:
        清理的 Agent 数量
    """
    registry = await load_chat_registry(chat_key)
    cleaned = 0

    # 清理 completed_agents 列表中的记录
    cleaned = len(registry.completed_agents)
    registry.completed_agents = []

    if cleaned > 0:
        await save_chat_registry(chat_key, registry)
        logger.info(f"清理了 {cleaned} 个已完成的 Agent 记录")

    return cleaned


# ==================== 重试和分支功能 ====================


async def reset_failed_agent(agent_id: str, chat_key: str) -> Optional[WebDevAgent]:
    """重置失败的 Agent，使其可以重新运行

    注意：此函数仅重置状态，不会启动工作循环。
    需要调用 agent_runner.start_agent_task() 来启动。

    Args:
        agent_id: Agent ID
        chat_key: 会话 key

    Returns:
        重置后的 Agent 实例，如果 Agent 不存在返回 None
    """
    registry = await load_chat_registry(chat_key)
    agent = registry.get_agent(agent_id)

    if not agent:
        # 尝试从已完成列表查找（可能已被移除但记录还在）
        logger.warning(f"Agent {agent_id} 不在活跃列表中")
        return None

    if agent.status != AgentStatus.FAILED:
        logger.warning(f"Agent {agent_id} 不是失败状态，无法重试")
        return None

    # 重置状态
    agent.status = AgentStatus.PENDING
    agent.error_message = None
    agent.progress_percent = 0
    agent.current_step = "准备重试"
    agent.last_active_time = int(time.time())

    await save_chat_registry(chat_key, registry)
    logger.info(f"重置失败的 Agent: {agent_id}")
    return agent


async def fork_agent(
    source_agent_id: str,
    chat_key: str,
    new_requirement: str,
    new_difficulty: Optional[int] = None,
) -> Tuple[Optional[WebDevAgent], Optional[str]]:
    """基于现有 Agent 创建分支

    复制源 Agent 的 HTML 成果和模板变量到新 Agent。

    Args:
        source_agent_id: 源 Agent ID
        chat_key: 会话 key
        new_requirement: 新的需求描述
        new_difficulty: 新的难度评分（可选，默认继承源 Agent）

    Returns:
        (新 Agent 实例, 错误信息)
    """
    # 获取源 Agent
    source_agent = await get_agent(source_agent_id, chat_key, update_access=False)
    if not source_agent:
        return None, f"源 Agent {source_agent_id} 不存在"

    if not source_agent.current_html:
        return None, f"源 Agent {source_agent_id} 没有 HTML 成果可复制"

    # 使用源 Agent 的难度或指定的新难度
    difficulty = (
        new_difficulty if new_difficulty is not None else source_agent.difficulty
    )

    # 创建新 Agent
    new_agent, error = await create_agent(chat_key, new_requirement, difficulty)
    if error:
        return None, error
    if not new_agent:
        return None, "创建新 Agent 失败"

    # 复制 HTML 和相关信息
    new_agent.current_html = source_agent.current_html
    new_agent.template_vars = source_agent.template_vars.copy()
    new_agent.page_title = source_agent.page_title
    new_agent.page_description = source_agent.page_description

    # 添加分支说明到消息
    new_agent.add_message(
        msg_type=MessageType.INSTRUCTION,
        sender="main",
        content=f"[基于 {source_agent_id} 分支] {new_requirement}",
    )

    await update_agent(new_agent)
    logger.info(f"从 {source_agent_id} 分支创建新 Agent: {new_agent.agent_id}")
    return new_agent, None
