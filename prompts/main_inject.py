"""主 Agent 视角提示词注入

为主 Agent 提供当前所有活跃子 Agent 的状态视图。
"""

import time

from nekro_agent.api.schemas import AgentCtx

from ..models import AgentStatus
from ..plugin import config
from ..services.agent_pool import auto_archive_expired_agents, load_chat_registry


async def inject_webapp_status(_ctx: AgentCtx) -> str:
    """注入 WebApp Agent 系统状态到主 Agent 提示词

    Args:
        _ctx: Agent 上下文

    Returns:
        注入的提示词内容
    """
    # 先检查并自动归档超时的 Agent
    await auto_archive_expired_agents(_ctx.chat_key)

    registry = await load_chat_registry(_ctx.chat_key)

    # 分类 Agent
    working_agents = {
        agent_id: agent
        for agent_id, agent in registry.active_agents.items()
        if agent.is_working()
    }
    confirmed_agents = {
        agent_id: agent
        for agent_id, agent in registry.active_agents.items()
        if agent.status == AgentStatus.WAITING_CONFIRM
    }

    prompt_parts: list[str] = []

    # 标题（根据身份呈现模式选择）
    total_active = len(registry.active_agents)
    if config.TRANSPARENT_SUB_AGENT:
        # 透明式：明确是助手团队
        if total_active > 0:
            prompt_parts.append(f"## 🤖 网页开发助手团队 ({total_active} 个任务)\n")
        else:
            prompt_parts.append("## 🤖 网页开发助手团队\n")
    else:
        # 沉浸式：作为自己的工作
        if total_active > 0:
            prompt_parts.append(f"## 🎯 当前网页开发工作 ({total_active} 项)\n")
        else:
            prompt_parts.append("## 🎯 网页开发工作\n")

    # 正在工作的 Agent
    if working_agents:
        prompt_parts.append(f"### 📋 进行中 ({len(working_agents)})")
        for agent_id, agent in working_agents.items():
            prompt_parts.append(_format_agent_status(agent_id, agent))
        prompt_parts.append("")

    # 已确认待归档的 Agent
    if confirmed_agents:
        prompt_parts.append(f"### ✅ 已完成待归档 ({len(confirmed_agents)})")
        for agent_id, agent in confirmed_agents.items():
            elapsed = _format_elapsed(
                int(time.time()) - (agent.confirmed_time or agent.create_time),
            )
            url_info = f" | 🔗 {agent.deployed_url}" if agent.deployed_url else ""
            prompt_parts.append(
                f"- **[{agent_id}]** {agent.requirement[:40]}... | 确认于 {elapsed} 前{url_info}",
            )
        prompt_parts.append("")

    # 已归档任务数量提示
    archived_count = len(registry.completed_agents)
    if archived_count > 0:
        prompt_parts.append(f"📦 已归档任务: {archived_count} 个")

    # 身份呈现指导
    if total_active > 0:
        prompt_parts.append("")
        if config.TRANSPARENT_SUB_AGENT:
            prompt_parts.append(
                "💡 **呈现方式**: 向用户说明时，可以明确提及「网页开发助手」正在处理任务。",
            )
        else:
            prompt_parts.append(
                "💡 **呈现方式**: 向用户说明时，将这些工作描述为你自己正在进行的工作（如：「我正在帮你设计网页...」）。",
            )

    return "\n".join(prompt_parts)


def _format_agent_status(agent_id: str, agent) -> str:  # noqa: ANN001
    """格式化单个 Agent 状态"""
    # 状态图标
    status_icon = _get_status_icon(agent.status)

    # 计算工作时间
    elapsed_seconds = int(time.time()) - agent.create_time
    elapsed = _format_elapsed(elapsed_seconds)

    # 难度标识
    difficulty_badge = _get_difficulty_badge(agent.difficulty)

    lines = [
        f"\n#### {status_icon} [{agent_id}] {agent.status.value} {difficulty_badge}",
        f"📝 **需求**: {agent.requirement[:80]}{'...' if len(agent.requirement) > 80 else ''}",
        f"📊 **进度**: {agent.progress_percent}% | ⏱️ {elapsed}",
    ]

    if agent.current_step:
        lines.append(f"🔧 **当前**: {agent.current_step}")

    # 预览链接
    if agent.deployed_url:
        lines.append(f"🔗 **预览**: {agent.deployed_url}")

    # 模板变量
    if agent.template_vars:
        var_keys = ", ".join(agent.template_vars.keys())
        lines.append(f"📦 **变量**: {len(agent.template_vars)} 个 ({var_keys})")

    # 最近一条通信
    if agent.messages:
        last_msg = agent.messages[-1]
        sender = "你" if last_msg.sender == "main" else "Agent"
        msg_preview = (
            last_msg.content[:60] + "..."
            if len(last_msg.content) > 60
            else last_msg.content
        )
        lines.append(f"💬 **最近**: [{sender}] {msg_preview}")

    # 等待反馈提示
    if agent.status == AgentStatus.WAITING_FEEDBACK:
        if config.TRANSPARENT_SUB_AGENT:
            lines.append(
                f'\n⚠️ **助手需要你的反馈！** 使用 `send_to_webapp_agent("{agent_id}", "反馈内容")` 回复',
            )
        else:
            lines.append(
                f'\n⚠️ **需要确认！** 使用 `send_to_webapp_agent("{agent_id}", "反馈内容")` 回复',
            )

    return "\n".join(lines)


def _get_status_icon(status: AgentStatus) -> str:
    """获取状态图标"""
    return {
        AgentStatus.PENDING: "⏳",
        AgentStatus.THINKING: "🤔",
        AgentStatus.CODING: "💻",
        AgentStatus.DEPLOYING: "🚀",
        AgentStatus.WAITING_FEEDBACK: "💬",
        AgentStatus.WAITING_CONFIRM: "✅",
        AgentStatus.COMPLETED: "✅",
        AgentStatus.FAILED: "❌",
        AgentStatus.CANCELLED: "🚫",
    }.get(status, "❓")


def _get_difficulty_badge(difficulty: int) -> str:
    """获取难度徽章"""
    if difficulty >= 8:
        return "🔴 困难"
    if difficulty >= 6:
        return "🟡 中等"
    if difficulty >= 4:
        return "🟢 简单"
    return "⚪ 基础"


def _format_elapsed(seconds: int) -> str:
    """格式化耗时"""
    if seconds < 60:
        return f"{seconds}秒"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}分{secs}秒"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}小时{mins}分"
