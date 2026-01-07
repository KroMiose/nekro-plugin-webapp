"""消息通信服务

负责主 Agent 和子 Agent 之间的消息传递。
"""

from nekro_agent.api.core import logger
from nekro_agent.api.message import message_service

from ..models import MessageType
from ..plugin import config
from .agent_pool import add_message_to_agent, get_agent


async def notify_main_agent(
    agent_id: str,
    chat_key: str,
    message: str,
    msg_type: MessageType = MessageType.PROGRESS,
    trigger: bool = False,
) -> bool:
    """子 Agent 向主 Agent 发送消息

    通过推送系统消息的方式通知主 Agent

    Args:
        agent_id: 子 Agent ID
        chat_key: 会话 key
        message: 消息内容
        msg_type: 消息类型
        trigger: 是否触发主 Agent 响应

    Returns:
        是否发送成功
    """
    agent = await get_agent(agent_id, chat_key)
    if not agent:
        logger.error(f"Agent {agent_id} 不存在，无法发送消息")
        return False

    # 根据身份呈现模式构造消息前缀
    type_emoji = {
        MessageType.QUESTION: "❓",
        MessageType.PROGRESS: "📊",
        MessageType.RESULT: "✅",
    }.get(msg_type, "📨")

    if config.TRANSPARENT_SUB_AGENT:
        # 透明式：显示助手身份
        system_message = (
            f"{type_emoji} [网页开发助手 {agent_id}] ({msg_type.value})\n{message}"
        )
    else:
        # 沉浸式：作为自己的工作进度
        type_desc = {
            MessageType.QUESTION: "需要确认",
            MessageType.PROGRESS: "工作进度",
            MessageType.RESULT: "完成",
        }.get(msg_type, "消息")
        system_message = f"{type_emoji} [{type_desc}]\n{message}"

    # 添加到 Agent 消息历史
    await add_message_to_agent(
        agent_id=agent_id,
        chat_key=chat_key,
        msg_type=msg_type,
        sender="webdev",
        content=message,
    )

    # 推送系统消息
    try:
        await message_service.push_system_message(
            chat_key=chat_key,
            agent_messages=system_message,
            trigger_agent=trigger,
        )
    except Exception as e:
        logger.error(f"推送系统消息失败: {e}")
        return False

    logger.info(f"Agent {agent_id} 向主 Agent 发送消息: {message[:50]}...")
    return True


async def send_to_webdev_agent(
    agent_id: str,
    chat_key: str,
    message: str,
    msg_type: MessageType = MessageType.INSTRUCTION,
) -> bool:
    """主 Agent 向子 Agent 发送消息

    Args:
        agent_id: 子 Agent ID
        chat_key: 会话 key
        message: 消息内容
        msg_type: 消息类型

    Returns:
        是否发送成功
    """
    agent = await get_agent(agent_id, chat_key)
    if not agent:
        logger.error(f"Agent {agent_id} 不存在，无法发送消息")
        return False

    if not agent.is_active():
        logger.warning(f"Agent {agent_id} 已不在活跃状态，无法发送消息")
        return False

    # 添加到 Agent 消息历史
    await add_message_to_agent(
        agent_id=agent_id,
        chat_key=chat_key,
        msg_type=msg_type,
        sender="main",
        content=message,
    )

    logger.info(f"主 Agent 向 {agent_id} 发送消息: {message[:50]}...")
    return True
