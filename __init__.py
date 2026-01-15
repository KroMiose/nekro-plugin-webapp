"""
# WebApp 快速部署插件

将 HTML 部署到 Cloudflare Workers，支持多 Agent 协作开发。
"""

from typing import Optional

from nekro_agent.api.schemas import AgentCtx
from nekro_agent.core import logger
from nekro_agent.services.plugin.base import SandboxMethodType

from . import commands as _commands  # noqa: F401
from .agent_core import SubAgentStatus
from .handlers import create_router  # noqa: F401
from .plugin import config, plugin
from .services import (
    cancel_agent_task,
    confirm_agent_task,
    create_agent,
    generate_status,
    get_agent,
    send_to_sub,
    start_agent_task,
    stop_all_tasks,
    update_agent,
    wake_up_agent,
)
from .services.task_tracer import TaskTracer

__all__ = ["plugin"]


# ==================== 沙盒方法 ====================


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "创建网页开发Agent")
async def create_webapp_agent(
    _ctx: AgentCtx,
    requirement: str,
    difficulty: int,
    template_vars: Optional[dict[str, str]] = None,
) -> str:
    """创建网页开发 Agent

    ⚠️ **关键：每个 Agent 是完全独立的上下文空间**

    Agent 无法看到：
    - 你之前发送的消息或对话历史
    - 其他 Agent 的内容或产物
    - 任何"通用知识"的假设

    ✅ 必须在 requirement 中包含：
    - 完整的功能需求描述（不要假设 Agent 知道聊天上下文）
    - 可选的技术偏好（仅作为建议，除非用户要求否则不允许特殊指定，底层技术栈固定为 React 无法修改，架构师有权根据其环境限制选择可用的技术栈）
    - 期望的输出形式（静态页面、交互式应用等）
    - 所有必要的业务要求和数据格式
    - 一次性安排完整任务，不要害怕任务过大，Agent 会自行拆分任务给子 Agent (工程师或内容创作者等) 进行工作

    ❌ 禁止的做法：
    - "按照之前讨论的方案实现" → Agent 看不到之前的讨论
    - "参考上一个 Agent 的代码" → Agent 之间相互隔离
    - 模糊的需求如 "做一个好看的页面" → 缺乏具体规格
    - 先做一个 Demo/框架 ，随后再提供更多项目数据等（这会导致大量不必要的接口与数据协议沟通和错乱风险）

    **Environment Variables**: (仅在必要时使用，禁止用于传递复杂逻辑结构数据)
    - Pass variables in `template_vars` (e.g. `{"API_KEY": "xxx", "HERO_IMG": "data:image..."}`).
    - In Agent instructions, tell them to use `process.env.VAR_NAME` (e.g. `process.env.API_KEY`).
    - Note: Large assets (like base64 images) injected this way are compiled into the bundle.

    Args:
        requirement: 完整的网页需求描述（必须自包含所有必要信息）
        difficulty: 难度 1-5（影响使用的模型，默认 3）
        template_vars: 可选的模板变量 {"key": "value"}，可注入 base64 图片
    """
    if not requirement or not requirement.strip():
        raise ValueError("需求描述不能为空")
    if not config.WORKER_URL or not config.ACCESS_KEY:
        raise ValueError("未配置 Worker 地址或访问密钥")

    difficulty = max(1, min(5, difficulty))
    agent = await create_agent(_ctx.chat_key, requirement.strip(), difficulty)

    if template_vars:
        for k, v in template_vars.items():
            agent.set_template_var(str(k), str(v))
        await update_agent(agent)

    # 创建任务追踪器
    tracer = TaskTracer(
        chat_key=_ctx.chat_key,
        root_agent_id=agent.agent_id,
        task_description=requirement.strip(),
        plugin_data_dir=str(plugin.get_plugin_data_dir()),
    )

    await start_agent_task(agent.agent_id, _ctx.chat_key, tracer)

    diff_str = "🟢简单" if difficulty < 3 else "🟡中等" if difficulty < 4 else "🔴困难"
    model_info = (
        " (高级模型)"
        if difficulty >= config.DIFFICULTY_THRESHOLD and config.ADVANCED_MODEL_GROUP
        else ""
    )

    if config.TRANSPARENT_SUB_AGENT:
        return f"✅ 已派遣助手 [{agent.agent_id}] 处理任务\n📝 {requirement[:80]}...\n📊 难度: {diff_str} ({difficulty}/5){model_info}"
    return f"✅ 开始处理网页任务\n📝 {requirement[:80]}...\n📊 {diff_str} ({difficulty}/5){model_info}"


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "向Agent发送消息")
async def send_to_webapp_agent(
    _ctx: AgentCtx,
    agent_id: str,
    message: str,
) -> str:
    """向 Agent 发送反馈消息

    ⚠️ **记住：Agent 只能看到它自己的上下文**

    Agent 能看到的：
    - 自己的任务描述
    - 自己之前的工作产物和模板
    - 通过此方法发送的反馈消息

    Agent 看不到的：
    - 你与用户的对话历史
    - 其他 Agent 的内容
    - 你没有明确告诉它的任何信息

    ✅ 发送反馈时应包含：
    - 具体的修改要求（哪里要改、改成什么）
    - 问题的具体描述（截图信息、错误现象）
    - 任何必要的额外上下文

    ⚠️ !!!注意：由于你无法查看 Agent 的真实代码产出，你被严格禁止直接提供任何实现技术相关的指导！只描述你的业务需求！如果无法实现如实反馈给用户!!!

    Args:
        agent_id: Agent ID
        message: 反馈消息（应包含完整的修改指导，内容严谨，不要代入人设语气）
    """
    if not agent_id or not message:
        raise ValueError("Agent ID 和消息不能为空")

    agent = await get_agent(agent_id.strip(), _ctx.chat_key)
    if not agent:
        raise ValueError(f"Agent {agent_id} 不存在")

    # 允许唤醒已完成的 Agent (Resurrection)
    if agent.status == SubAgentStatus.COMPLETED:
        # 创建任务追踪器 (Resurrection)
        tracer = TaskTracer(
            chat_key=_ctx.chat_key,
            root_agent_id=agent.agent_id,
            task_description=agent.task or "Resurrected Task",
            plugin_data_dir=str(plugin.get_plugin_data_dir()),
        )
        
        tracer.log_event("AGENT_RESURRECT", agent.agent_id, f"唤醒已完成的 Agent {agent_id} 处理新反馈")

        agent.status = SubAgentStatus.PENDING
        agent.error_message = None
        # 重置完成时间，标记为重新打开
        agent.complete_time = None
        agent.iteration_count = 0  # 可选：重置迭代计数以给予更多尝试机会
        await update_agent(agent)

        # 必须重启任务循环
        await start_agent_task(agent.agent_id, _ctx.chat_key, tracer)

    elif not agent.is_active():
        raise ValueError(f"Agent {agent_id} 已结束且不可恢复 ({agent.status.value})")

    await send_to_sub(_ctx.chat_key, agent_id.strip(), message.strip())
    # 如果任务已经在运行，wake_up 会通知它；如果是刚重启，这步也无害
    await wake_up_agent(agent_id.strip(), _ctx.chat_key, message.strip())
    return f"✅ 已发送反馈给 [{agent_id}] (Agent 已自动唤醒)"


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "确认Agent完成")
async def confirm_webapp_agent(_ctx: AgentCtx, agent_id: str) -> str:
    """确认 Agent 任务完成

    Args:
        agent_id: Agent ID
    """
    if not agent_id:
        raise ValueError("请指定 Agent ID")

    agent = await get_agent(agent_id.strip(), _ctx.chat_key)
    if not agent:
        raise ValueError(f"Agent {agent_id} 不存在")
    if agent.status == SubAgentStatus.COMPLETED:
        return f"Agent {agent_id} 已完成"

    await confirm_agent_task(agent_id.strip(), _ctx.chat_key)

    result = f"✅ Agent [{agent_id}] 已确认完成"
    if agent.deployed_url:
        result += f"\n🔗 {agent.deployed_url}"
    return result


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "取消Agent")
async def cancel_webapp_agent(_ctx: AgentCtx, agent_id: str, reason: str = "") -> str:
    """取消 Agent 任务

    Args:
        agent_id: Agent ID
        reason: 取消原因
    """
    if not agent_id:
        raise ValueError("请指定 Agent ID")

    agent = await get_agent(agent_id.strip(), _ctx.chat_key)
    if not agent:
        raise ValueError(f"Agent {agent_id} 不存在")
    if not agent.is_active():
        raise ValueError(f"Agent {agent_id} 已结束")

    await cancel_agent_task(agent_id.strip(), _ctx.chat_key, reason)

    result = f"✅ Agent [{agent_id}] 已取消"
    if reason:
        result += f"\n原因: {reason}"
    if agent.deployed_url:
        result += f"\n页面仍可访问: {agent.deployed_url}"
    return result


@plugin.mount_sandbox_method(SandboxMethodType.TOOL, "获取Agent预览链接")
async def get_webapp_preview(_ctx: AgentCtx, agent_id: str) -> str:
    """获取预览链接

    Args:
        agent_id: Agent ID
    """
    if not agent_id:
        raise ValueError("请指定 Agent ID")

    agent = await get_agent(agent_id.strip(), _ctx.chat_key)
    if not agent:
        raise ValueError(f"Agent {agent_id} 不存在")

    if agent.deployed_url:
        return f"🔗 {agent.deployed_url}"
    return f"Agent [{agent_id}] 尚未部署 (状态: {agent.status.value})"


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "设置模板变量")
async def set_webapp_template_var(
    _ctx: AgentCtx,
    agent_id: str,
    key: str,
    value: str,
) -> str:
    """设置模板变量

    Args:
        agent_id: Agent ID
        key: 变量名
        value: 变量值
    """
    if not agent_id or not key or not value:
        raise ValueError("参数不能为空")

    agent = await get_agent(agent_id.strip(), _ctx.chat_key)
    if not agent:
        raise ValueError(f"Agent {agent_id} 不存在")

    agent.set_template_var(key.strip(), value)
    await update_agent(agent)
    return f"✅ 已设置 {key} ({len(value)}字符)"


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "重试失败Agent")
async def retry_webapp_agent(_ctx: AgentCtx, agent_id: str) -> str:
    """重试失败的 Agent

    Args:
        agent_id: Agent ID
    """
    if not agent_id:
        raise ValueError("请指定 Agent ID")

    agent = await get_agent(agent_id.strip(), _ctx.chat_key)
    if not agent:
        raise ValueError(f"Agent {agent_id} 不存在")
    if agent.status != SubAgentStatus.FAILED:
        raise ValueError(f"Agent {agent_id} 不是失败状态")

    agent.status = SubAgentStatus.PENDING
    agent.error_message = None
    agent.iteration_count = 0
    await update_agent(agent)
    
    # 创建任务追踪器 (Retry)
    tracer = TaskTracer(
        chat_key=_ctx.chat_key,
        root_agent_id=agent.agent_id,
        task_description=agent.task or "Retried Task",
        plugin_data_dir=str(plugin.get_plugin_data_dir()),
    )
    
    await start_agent_task(agent.agent_id, _ctx.chat_key, tracer)
    return f"✅ Agent [{agent_id}] 已重启"


# ==================== 提示词注入 ====================


@plugin.mount_prompt_inject_method("webapp_status")
async def webapp_status_inject(_ctx: AgentCtx) -> str:
    """注入 Agent 状态"""
    return await generate_status(_ctx.chat_key)


# ==================== 生命周期 ====================


@plugin.on_enabled()
async def _startup() -> None:
    """插件启动：恢复未完成任务"""
    # 启动时检查 Node 环境，确保本地编译器可用
    # 启动时检查 Node 环境，确保本地编译器可用
    try:
        from .services import node_manager

        node_path = await node_manager.get_node_executable()
        logger.info(f"WebApp 插件已启用 (Node.js verified at {node_path})")
    except Exception as e:
        logger.error(f"WebApp 插件启动警告: 本地编译环境自检失败 - {e}")
        logger.error(
            "请确保系统安装了 Node.js (>=16)，或者允许网络连接以下载独立运行时！",
        )

    # TODO: 遍历所有会话恢复 PENDING/WORKING 状态的 Agent


@plugin.on_disabled()
async def _cleanup() -> None:
    """插件停用：停止所有任务"""
    count = await stop_all_tasks()
    if count:
        logger.info(f"已停止 {count} 个 Agent 任务")
