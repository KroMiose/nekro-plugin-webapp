"""
# WebApp 快速部署插件

将 HTML 内容快速部署到 Cloudflare Workers 并生成在线访问链接。
支持多 Agent 异步协作模式进行网页开发。

## 主要功能

- **多 Agent 协作**：创建独立的网页开发 Agent 异步工作
- **智能难度评估**：AI 自动评估任务难度，复杂任务使用高级模型
- **实时状态感知**：主 Agent 可实时查看子 Agent 的工作进度
- **双向通信**：主 Agent 和子 Agent 可以相互发送消息和反馈
- **AI 一键部署**：通过简单的 API 调用将 HTML 部署为在线网页
- **Web 管理界面**：可视化管理已部署的页面
"""

from typing import Optional

from nekro_agent.api.schemas import AgentCtx
from nekro_agent.core import logger
from nekro_agent.services.plugin.base import SandboxMethodType

from . import commands as _commands  # noqa: F401 - 注册管理命令
from .handlers import create_router  # noqa: F401
from .models import AgentStatus, MessageType
from .plugin import config, plugin
from .prompts import inject_webapp_status
from .services import (
    archive_agent,
    cancel_agent,
    confirm_agent,
    create_agent,
    delete_agent_template_var,
    fork_agent,
    get_active_agents_for_chat,
    get_agent,
    get_all_chat_keys_with_agents,
    get_chat_registry,
    get_resumable_agents,
    register_active_chat_key,
    reset_failed_agent,
    send_to_webdev_agent,
    set_agent_template_var,
    start_agent_task,
    stop_all_tasks,
    update_agent,
    wake_up_agent,
)

__all__ = ["plugin"]


# ==================== 主 Agent 调用的方法 ====================


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "创建网页开发Agent")
async def create_webapp_agent(
    _ctx: AgentCtx,
    requirement: str,
    difficulty: int,
    template_vars: Optional[dict[str, str]] = None,
) -> str:
    """创建一个新的网页开发 Agent 来处理网页开发任务

    当用户需要创建网页时，调用此方法创建一个独立的子 Agent 来异步完成开发工作。
    子 Agent 会自动开始工作，你可以通过提示词注入查看其进度。

    Args:
        requirement: 详细的网页需求描述，包括功能要求、设计风格、内容等
        difficulty: 任务难度评分 (1-10)，由你根据需求复杂度判断
            - 1-3: 简单任务（静态展示页、简单介绍页）
            - 4-6: 中等任务（响应式布局、基础交互）
            - 7-10: 困难任务（复杂动画、数据可视化、游戏等）
        template_vars: 模板变量字典，如 {"logo": "base64...", "name": "张三"}
            子 Agent 可在 HTML 中使用 {{key}} 占位符引用这些变量，部署时自动替换

    Returns:
        创建结果，包含新 Agent 的 ID

    Examples:
        # 创建一个简历页面
        result = create_webapp_agent("帮我创建一个个人简历页面，要求现代简约风格，深色主题", 4)

        # 创建带模板变量的页面
        result = create_webapp_agent(
            "创建个人主页，使用提供的 logo 和名字",
            5,
            {"logo_base64": "data:image/png;base64,...", "name": "张三"}
        )
    """
    if not requirement or not requirement.strip():
        raise ValueError("需求描述不能为空")

    if not config.WORKER_URL:
        raise ValueError("未配置 Worker 地址，请先在插件配置中设置 WORKER_URL")
    if not config.ACCESS_KEY:
        raise ValueError("未配置访问密钥，请先配置 ACCESS_KEY")

    # 验证难度范围
    difficulty = max(1, min(10, difficulty))

    # 创建 Agent
    agent, error = await create_agent(_ctx.chat_key, requirement.strip(), difficulty)
    if error:
        raise RuntimeError(f"创建失败: {error}")
    if not agent:
        raise RuntimeError("创建失败: 未知错误")

    # 设置模板变量
    if template_vars:
        for key, value in template_vars.items():
            agent.set_template_var(str(key), str(value))
        await update_agent(agent)

    # 启动 Agent 工作
    await start_agent_task(agent.agent_id, _ctx.chat_key)

    # 模型信息
    model_info = ""
    if difficulty >= config.DIFFICULTY_THRESHOLD and config.ADVANCED_MODEL_GROUP:
        model_info = " (使用高级模型)"

    difficulty_desc = {
        range(1, 4): "🟢 简单",
        range(4, 7): "🟡 中等",
        range(7, 11): "🔴 困难",
    }
    diff_str = next((v for k, v in difficulty_desc.items() if difficulty in k), "")

    # 模板变量信息
    vars_info = ""
    if template_vars:
        vars_info = f"\n📦 模板变量: {len(template_vars)} 个 ({', '.join(template_vars.keys())})"

    # 根据身份呈现模式选择文案
    if config.TRANSPARENT_SUB_AGENT:
        # 透明式：明确告知是助手在工作
        return f"""✅ 已派遣网页开发助手 [{agent.agent_id}] 处理任务

📝 任务需求: {requirement[:100]}{"..." if len(requirement) > 100 else ""}
📊 难度评估: {diff_str} ({difficulty}/10){model_info}{vars_info}"""
    # 沉浸式：作为自己的工作
    return f"""✅ 我开始处理网页开发任务了

📝 任务: {requirement[:100]}{"..." if len(requirement) > 100 else ""}
📊 预计难度: {diff_str}{model_info}{vars_info}"""


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "向Agent发送消息")
async def send_to_webapp_agent_method(
    _ctx: AgentCtx,
    agent_id: str,
    message: str,
    msg_type: str = "feedback",
) -> str:
    """向指定的网页开发 Agent 发送消息

    用于向正在工作的子 Agent 发送指令、反馈或回答问题。
    消息会被记录到 Agent 的通信历史中，并唤醒 Agent 继续工作。

    Args:
        agent_id: 目标 Agent ID，如 "WEB-a3f8"
        message: 消息内容
        msg_type: 消息类型
            - "instruction": 新的指令或需求变更
            - "feedback": 对现有工作的修改反馈
            - "answer": 回答 Agent 的问题

    Returns:
        发送结果
    """
    if not agent_id or not agent_id.strip():
        raise ValueError("请指定 Agent ID")
    if not message or not message.strip():
        raise ValueError("消息内容不能为空")

    type_mapping = {
        "instruction": MessageType.INSTRUCTION,
        "feedback": MessageType.FEEDBACK,
        "answer": MessageType.ANSWER,
    }
    if msg_type not in type_mapping:
        raise ValueError(
            f"无效的消息类型: {msg_type}，支持: instruction, feedback, answer",
        )

    agent = await get_agent(agent_id.strip(), _ctx.chat_key)
    if not agent:
        raise ValueError(f"Agent {agent_id} 不存在")
    if not agent.is_active():
        raise ValueError(f"Agent {agent_id} 已不在活跃状态 ({agent.status.value})")

    # 如果是已确认状态，需要重新激活
    if agent.status == AgentStatus.WAITING_CONFIRM:
        from .services import update_agent_status

        await update_agent_status(
            agent_id.strip(),
            _ctx.chat_key,
            AgentStatus.WAITING_FEEDBACK,
        )

    success = await send_to_webdev_agent(
        agent_id=agent_id.strip(),
        chat_key=_ctx.chat_key,
        message=message.strip(),
        msg_type=type_mapping[msg_type],
    )
    if not success:
        raise RuntimeError("发送消息失败")

    await wake_up_agent(agent_id.strip(), _ctx.chat_key)

    type_desc = {
        "instruction": "新指令",
        "feedback": "修改反馈",
        "answer": "问题回答",
    }.get(msg_type, "消息")
    return f"✅ 已向 Agent [{agent_id}] 发送{type_desc}，Agent 将继续工作"


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "确认Agent任务完成")
async def confirm_webapp_agent(
    _ctx: AgentCtx,
    agent_id: str,
    force_archive: bool = False,
) -> str:
    """确认指定 Agent 的任务已完成

    当对 Agent 的工作结果满意时，调用此方法确认完成。
    确认后 Agent 仍保留在列表中，可继续接收反馈。
    超过设定时间未访问后自动归档，或在创建新任务时自动归档。

    Args:
        agent_id: 目标 Agent ID
        force_archive: 是否强制归档（不保留，直接移出活跃列表）

    Returns:
        确认结果
    """
    if not agent_id or not agent_id.strip():
        raise ValueError("请指定 Agent ID")

    agent = await get_agent(agent_id.strip(), _ctx.chat_key)
    if not agent:
        raise ValueError(f"Agent {agent_id} 不存在")

    if agent.status == AgentStatus.COMPLETED:
        return f"Agent {agent_id} 已归档"

    if agent.status == AgentStatus.WAITING_CONFIRM:
        if force_archive:
            archived = await archive_agent(agent_id.strip(), _ctx.chat_key)
            if not archived:
                raise RuntimeError("归档失败")
            result = f"✅ Agent [{agent_id}] 已强制归档"
            if archived.deployed_url:
                result += f"\n\n页面链接: {archived.deployed_url}"
            return result
        return f"Agent {agent_id} 已确认完成，等待自动归档。如需立即归档，使用 force_archive=True"

    confirmed = await confirm_agent(agent_id.strip(), _ctx.chat_key)
    if not confirmed:
        raise RuntimeError("确认失败")

    result = f"✅ Agent [{agent_id}] 已确认完成，任务已标记为完成，仍保留在列表中"
    if confirmed.deployed_url:
        result += f"\n🔗 页面链接: {confirmed.deployed_url}"
    return result


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "取消Agent")
async def cancel_webapp_agent_method(
    _ctx: AgentCtx,
    agent_id: str,
    reason: str = "",
) -> str:
    """取消指定 Agent 的任务

    当不再需要某个 Agent 的工作时，调用此方法取消。
    已部署的页面不会被删除。

    Args:
        agent_id: 目标 Agent ID
        reason: 取消原因（可选）

    Returns:
        取消结果
    """
    if not agent_id or not agent_id.strip():
        raise ValueError("请指定 Agent ID")

    agent = await get_agent(agent_id.strip(), _ctx.chat_key)
    if not agent:
        raise ValueError(f"Agent {agent_id} 不存在")
    if not agent.is_active():
        raise ValueError(f"Agent {agent_id} 已不在活跃状态 ({agent.status.value})")

    cancelled = await cancel_agent(agent_id.strip(), _ctx.chat_key, reason)
    if not cancelled:
        raise RuntimeError("取消失败")

    result = f"✅ Agent [{agent_id}] 已取消"
    if reason:
        result += f"\n原因: {reason}"
    if cancelled.deployed_url:
        result += f"\n\n已部署的页面仍可访问: {cancelled.deployed_url}"
    return result


@plugin.mount_sandbox_method(SandboxMethodType.TOOL, "获取Agent预览链接")
async def get_webapp_preview(_ctx: AgentCtx, agent_id: str) -> str:
    """获取指定 Agent 的网页预览链接

    Args:
        agent_id: 目标 Agent ID

    Returns:
        预览 URL 或状态说明
    """
    if not agent_id or not agent_id.strip():
        raise ValueError("请指定 Agent ID")

    agent = await get_agent(agent_id.strip(), _ctx.chat_key)
    if not agent:
        raise ValueError(f"Agent {agent_id} 不存在")

    if agent.deployed_url:
        return f"🔗 Agent [{agent_id}] 预览链接: {agent.deployed_url}"
    return f"Agent [{agent_id}] 尚未部署页面 (当前状态: {agent.status.value})"


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "设置模板变量")
async def set_webapp_template_var(
    _ctx: AgentCtx,
    agent_id: str,
    key: str,
    value: str,
) -> str:
    """设置或更新指定 Agent 的模板变量

    模板变量用于在 HTML 中传递大型内容（如 Base64 图片、长文本等）。
    子 Agent 可在 HTML 中使用 {{key}} 占位符，部署时自动替换为实际值。

    Args:
        agent_id: 目标 Agent ID
        key: 变量名（建议使用英文和下划线）
        value: 变量值（可以是任意字符串，包括 Base64 编码的图片）

    Returns:
        设置结果
    """
    if not agent_id or not agent_id.strip():
        raise ValueError("请指定 Agent ID")
    if not key or not key.strip():
        raise ValueError("变量名不能为空")

    agent = await get_agent(agent_id.strip(), _ctx.chat_key)
    if not agent:
        raise ValueError(f"Agent {agent_id} 不存在")
    if not agent.is_active():
        raise ValueError(f"Agent {agent_id} 已不在活跃状态 ({agent.status.value})")

    success = await set_agent_template_var(
        agent_id=agent_id.strip(),
        chat_key=_ctx.chat_key,
        key=key.strip(),
        value=value,
    )
    if not success:
        raise RuntimeError("设置失败")

    preview = value[:50] + "..." if len(value) > 50 else value
    return f"✅ 已设置 Agent [{agent_id}] 模板变量 `{key}` ({len(value)} 字符)\n预览: {preview}"


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "删除模板变量")
async def delete_webapp_template_var(
    _ctx: AgentCtx,
    agent_id: str,
    key: str,
) -> str:
    """删除指定 Agent 的模板变量

    Args:
        agent_id: 目标 Agent ID
        key: 变量名

    Returns:
        删除结果
    """
    if not agent_id or not agent_id.strip():
        raise ValueError("请指定 Agent ID")
    if not key or not key.strip():
        raise ValueError("变量名不能为空")

    agent = await get_agent(agent_id.strip(), _ctx.chat_key)
    if not agent:
        raise ValueError(f"Agent {agent_id} 不存在")

    success = await delete_agent_template_var(
        agent_id=agent_id.strip(),
        chat_key=_ctx.chat_key,
        key=key.strip(),
    )
    if not success:
        raise ValueError(f"删除失败，变量 `{key}` 可能不存在")

    return f"✅ 已删除 Agent [{agent_id}] 模板变量 `{key}`"


@plugin.mount_sandbox_method(SandboxMethodType.TOOL, "列出模板变量")
async def list_webapp_template_vars(_ctx: AgentCtx, agent_id: str) -> str:
    """列出指定 Agent 的所有模板变量

    Args:
        agent_id: 目标 Agent ID

    Returns:
        模板变量列表
    """
    if not agent_id or not agent_id.strip():
        raise ValueError("请指定 Agent ID")

    agent = await get_agent(agent_id.strip(), _ctx.chat_key)
    if not agent:
        raise ValueError(f"Agent {agent_id} 不存在")

    if not agent.template_vars:
        return f"Agent [{agent_id}] 没有模板变量"

    lines = [f"📦 Agent [{agent_id}] 模板变量 ({len(agent.template_vars)} 个):\n"]
    for key, preview in agent.get_all_template_previews(
        config.TEMPLATE_VAR_PREVIEW_LEN,
    ).items():
        lines.append(f"- `{key}`: {preview}")
    return "\n".join(lines)


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "重试Agent")
async def retry_webapp_agent(_ctx: AgentCtx, agent_id: str) -> str:
    """重试失败的 Agent

    当 Agent 因错误失败时，可以使用此方法重新启动。
    会重置 Agent 状态并重新开始工作循环。

    Args:
        agent_id: 失败的 Agent ID

    Returns:
        重试结果
    """
    if not agent_id or not agent_id.strip():
        raise ValueError("请指定 Agent ID")

    agent = await get_agent(agent_id.strip(), _ctx.chat_key)
    if not agent:
        raise ValueError(f"Agent {agent_id} 不存在")
    if agent.status != AgentStatus.FAILED:
        raise ValueError(
            f"Agent {agent_id} 不是失败状态，无法重试 (当前: {agent.status.value})",
        )

    # 重置并重启
    reset_agent = await reset_failed_agent(agent_id.strip(), _ctx.chat_key)
    if not reset_agent:
        raise RuntimeError("重置失败")

    # 启动工作循环
    await start_agent_task(agent_id.strip(), _ctx.chat_key)

    return f"✅ Agent [{agent_id}] 已重置并重新启动工作"


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "分支Agent")
async def fork_webapp_agent_method(
    _ctx: AgentCtx,
    agent_id: str,
    new_requirement: str,
    difficulty: Optional[int] = None,
) -> str:
    """基于现有 Agent 成果创建新 Agent

    复制源 Agent 的 HTML 代码和模板变量，在此基础上开发新需求。
    适用于需要在已有页面上继续扩展或创建变体的场景。

    Args:
        agent_id: 源 Agent ID（需要有 HTML 成果）
        new_requirement: 新的需求描述
        difficulty: 新任务难度（可选，默认继承源 Agent）

    Returns:
        创建结果

    Examples:
        # 在现有页面基础上添加新功能
        fork_webapp_agent("WEB-a3f8", "在现有页面上添加一个联系表单")

        # 创建页面变体
        fork_webapp_agent("WEB-a3f8", "将现有页面改为浅色主题", 4)
    """
    if not agent_id or not agent_id.strip():
        raise ValueError("请指定源 Agent ID")
    if not new_requirement or not new_requirement.strip():
        raise ValueError("新需求描述不能为空")

    # 验证难度范围
    if difficulty is not None:
        difficulty = max(1, min(10, difficulty))

    # 创建分支
    new_agent, error = await fork_agent(
        source_agent_id=agent_id.strip(),
        chat_key=_ctx.chat_key,
        new_requirement=new_requirement.strip(),
        new_difficulty=difficulty,
    )
    if error:
        raise RuntimeError(f"分支失败: {error}")
    if not new_agent:
        raise RuntimeError("创建分支失败")

    # 启动新 Agent
    await start_agent_task(new_agent.agent_id, _ctx.chat_key)

    difficulty_desc = {
        range(1, 4): "🟢 简单",
        range(4, 7): "🟡 中等",
        range(7, 11): "🔴 困难",
    }
    diff_str = next(
        (v for k, v in difficulty_desc.items() if new_agent.difficulty in k),
        "",
    )

    return f"""✅ 从 [{agent_id}] 分支创建新 Agent [{new_agent.agent_id}]

📝 新需求: {new_requirement[:100]}{"..." if len(new_requirement) > 100 else ""}
📊 难度: {diff_str} ({new_agent.difficulty}/10)
📦 继承了源 Agent 的 HTML 代码和 {len(new_agent.template_vars)} 个模板变量"""


# ==================== 提示词注入 ====================


@plugin.mount_prompt_inject_method("webapp_status")
async def webapp_status_inject(_ctx: AgentCtx) -> str:
    """注入 WebApp Agent 系统状态到主 Agent 提示词"""
    return await inject_webapp_status(_ctx)


# ==================== 启动和清理 ====================


async def _resume_incomplete_agents() -> None:
    """恢复未完成的任务（内部函数）"""
    try:
        chat_keys = await get_all_chat_keys_with_agents()
        resumed_count = 0

        for chat_key in chat_keys:
            agents = await get_resumable_agents(chat_key)
            for agent in agents:
                try:
                    await start_agent_task(agent.agent_id, chat_key)
                    resumed_count += 1
                    logger.info(f"恢复 Agent 任务: {agent.agent_id}")
                except Exception as e:
                    logger.warning(f"恢复 Agent {agent.agent_id} 失败: {e}")

        if resumed_count > 0:
            logger.info(f"WebApp 插件启动完成，恢复了 {resumed_count} 个未完成的任务")
        else:
            logger.debug("WebApp 插件启动完成，无需恢复的任务")
    except Exception as e:
        logger.warning(f"WebApp 插件启动时恢复任务失败: {e}")


@plugin.mount_cleanup_method()
async def clean_up() -> None:
    """清理插件资源，停止所有运行中的任务"""
    try:
        stopped_count = await stop_all_tasks()
        if stopped_count > 0:
            logger.info(f"WebApp 插件已清理 {stopped_count} 个运行中的任务")
        else:
            logger.info("WebApp 插件资源已清理")
    except Exception as e:
        logger.warning(f"WebApp 插件清理失败: {e}")


# 插件加载时调度恢复任务
def _schedule_resume_on_load() -> None:
    """在插件加载时调度恢复任务"""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_resume_incomplete_agents())
    except RuntimeError:
        # 没有运行中的事件循环，跳过
        pass


_schedule_resume_on_load()

