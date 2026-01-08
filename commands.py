"""管理员命令

提供管理员用于查看和管理 WebApp Agent 系统的命令。
"""

import time

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from nekro_agent.adapters.onebot_v11.matchers.command import (
    command_guard,
    finish_with,
    on_command,
)

from .models import AgentStatus
from .services import (
    cancel_agent,
    clean_completed_agents,
    get_active_agents_for_chat,
    get_agent,
    get_chat_registry,
)


def _get_status_emoji(status: AgentStatus) -> str:
    """获取状态对应的 emoji"""
    return {
        AgentStatus.PENDING: "⏳",
        AgentStatus.THINKING: "🤔",
        AgentStatus.CODING: "💻",
        AgentStatus.DEPLOYING: "🚀",
        AgentStatus.WAITING_FEEDBACK: "💬",
        AgentStatus.COMPLETED: "✅",
        AgentStatus.FAILED: "❌",
        AgentStatus.CANCELLED: "🚫",
    }.get(status, "❓")


def _get_difficulty_badge(difficulty: int) -> str:
    """获取难度徽章"""
    if difficulty >= 8:
        return "🔴"
    if difficulty >= 6:
        return "🟡"
    if difficulty >= 4:
        return "🟢"
    return "⚪"


def _format_elapsed_time(start_time: int) -> str:
    """格式化耗时"""
    elapsed = int(time.time()) - start_time
    if elapsed < 60:
        return f"{elapsed}秒"
    if elapsed < 3600:
        return f"{elapsed // 60}分{elapsed % 60}秒"
    hours = elapsed // 3600
    minutes = (elapsed % 3600) // 60
    return f"{hours}小时{minutes}分"


def _format_timestamp(ts: int) -> str:
    """格式化时间戳"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


# ==================== 命令实现 ====================


@on_command(
    "webapp_list",
    aliases={"webapp-list", "webapp_ls", "webapp-ls", "wa_ls", "wa-ls", "wa_list", "wa-list"},
    priority=5,
    block=True,
).handle()
async def _(matcher: Matcher, event: MessageEvent, bot: Bot, arg: Message = CommandArg()):
    """列出当前会话的所有活跃 Agent"""
    _username, _cmd_content, chat_key, _chat_type = await command_guard(event, bot, arg, matcher)

    registry = await get_chat_registry(chat_key)

    if not registry.active_agents:
        await finish_with(matcher, message="当前会话没有活跃的网页开发 Agent")
        return

    lines = [f"📋 当前会话活跃的 Agent ({len(registry.active_agents)} 个):\n"]

    for agent_id, agent in registry.active_agents.items():
        status_emoji = _get_status_emoji(agent.status)
        diff_badge = _get_difficulty_badge(agent.difficulty)
        elapsed = _format_elapsed_time(agent.create_time)

        # HTML 大小信息
        html_size = f"{len(agent.current_html)}字符" if agent.current_html else "无"
        vars_count = len(agent.template_vars)

        lines.append(f"{status_emoji} [{agent_id}] {agent.status.value} {diff_badge}")
        lines.append(f"   需求: {agent.requirement[:40]}...")
        lines.append(f"   进度: {agent.progress_percent}% | 难度: {agent.difficulty}/10 | 耗时: {elapsed}")
        lines.append(f"   📄 HTML: {html_size} | 📦 变量: {vars_count}个")
        if agent.deployed_url:
            lines.append(f"   🔗 {agent.deployed_url}")
        lines.append("")

    lines.append("使用 webapp-info <agent_id> 查看详情")
    await finish_with(matcher, message="\n".join(lines))


@on_command(
    "webapp_info",
    aliases={"webapp-info", "webapp_i", "webapp-i", "wa_info", "wa-info"},
    priority=5,
    block=True,
).handle()
async def _(matcher: Matcher, event: MessageEvent, bot: Bot, arg: Message = CommandArg()):
    """查看指定 Agent 的详细信息"""
    _username, cmd_content, chat_key, _chat_type = await command_guard(event, bot, arg, matcher)

    if not cmd_content:
        await finish_with(matcher, message="请指定 Agent ID，如: webapp-info WEB-a3f8")
        return

    agent_id = cmd_content.strip()
    agent = await get_agent(agent_id, chat_key)

    if not agent:
        await finish_with(matcher, message=f"Agent {agent_id} 不存在")
        return

    diff_badge = _get_difficulty_badge(agent.difficulty)

    # 格式化详细信息
    lines = [f"📊 Agent [{agent_id}] 详细信息\n"]
    lines.append(f"🔸 状态: {agent.status.value}")
    lines.append(f"🔸 进度: {agent.progress_percent}%")
    lines.append(f"🔸 难度: {diff_badge} {agent.difficulty}/10")
    lines.append(f"🔸 当前步骤: {agent.current_step or '无'}")
    lines.append(f"🔸 迭代次数: {agent.iteration_count}")

    # 实现规模
    lines.append("")
    lines.append("📄 实现规模:")
    if agent.current_html:
        html_len = len(agent.current_html)
        lines.append(f"   HTML 大小: {html_len} 字符 ({html_len // 1024:.1f} KB)")
    else:
        lines.append("   HTML 大小: 无")
    lines.append(f"   模板变量: {len(agent.template_vars)} 个")
    if agent.template_vars:
        var_keys = ", ".join(agent.template_vars.keys())
        lines.append(f"   变量列表: {var_keys[:60]}{'...' if len(var_keys) > 60 else ''}")

    lines.append("")
    lines.append("📝 任务需求:")
    lines.append(agent.requirement)
    lines.append("")
    lines.append("⏱️ 时间信息:")
    lines.append(f"   创建: {_format_timestamp(agent.create_time)}")
    if agent.start_time:
        lines.append(f"   启动: {_format_timestamp(agent.start_time)}")
    lines.append(f"   最后活跃: {_format_timestamp(agent.last_active_time)}")
    if agent.complete_time:
        lines.append(f"   完成: {_format_timestamp(agent.complete_time)}")

    # 通信记录
    if agent.messages:
        lines.append("")
        lines.append(f"💬 通信记录 ({len(agent.messages)} 条，显示最近 {min(5, len(agent.messages))} 条):")
        for msg in agent.messages[-5:]:
            sender = "主Agent" if msg.sender == "main" else "子Agent"
            msg_time = time.strftime("%H:%M:%S", time.localtime(msg.timestamp))
            content_preview = msg.content[:50] + "..." if len(msg.content) > 50 else msg.content
            lines.append(f"   [{msg_time}] {sender}: {content_preview}")

    if agent.deployed_url:
        lines.append("")
        lines.append(f"🔗 预览链接: {agent.deployed_url}")

    if agent.error_message:
        lines.append("")
        lines.append(f"❌ 错误信息: {agent.error_message}")

    await finish_with(matcher, message="\n".join(lines))


@on_command("webapp_stats", aliases={"webapp-stats", "wa_stats", "wa-stats"}, priority=5, block=True).handle()
async def _(matcher: Matcher, event: MessageEvent, bot: Bot, arg: Message = CommandArg()):
    """查看当前会话统计信息"""
    _username, _cmd_content, chat_key, _chat_type = await command_guard(event, bot, arg, matcher)

    registry = await get_chat_registry(chat_key)

    # 统计各状态数量
    status_counts: dict[str, int] = {}
    total_active = 0
    total_difficulty = 0

    for agent in registry.active_agents.values():
        status_name = agent.status.value
        status_counts[status_name] = status_counts.get(status_name, 0) + 1
        if agent.is_active():
            total_active += 1
            total_difficulty += agent.difficulty

    avg_difficulty = total_difficulty / total_active if total_active > 0 else 0

    lines = ["📈 WebApp Agent 会话统计\n"]
    lines.append(f"🟢 当前活跃: {total_active} 个")
    lines.append(f"📊 平均难度: {avg_difficulty:.1f}/10")
    lines.append(f"📜 历史完成: {len(registry.completed_agents)} 个")

    if status_counts:
        lines.append("")
        lines.append("📋 状态分布:")
        for status, count in sorted(status_counts.items()):
            lines.append(f"   {status}: {count}")

    await finish_with(matcher, message="\n".join(lines))


@on_command("webapp_cancel", aliases={"webapp-cancel", "wa_cancel", "wa-cancel"}, priority=5, block=True).handle()
async def _(matcher: Matcher, event: MessageEvent, bot: Bot, arg: Message = CommandArg()):
    """取消指定 Agent"""
    _username, cmd_content, chat_key, _chat_type = await command_guard(event, bot, arg, matcher)

    if not cmd_content:
        await finish_with(matcher, message="请指定 Agent ID，如: webapp-cancel WEB-a3f8")
        return

    # 解析参数：agent_id [reason]
    parts = cmd_content.strip().split(maxsplit=1)
    agent_id = parts[0]
    reason = parts[1] if len(parts) > 1 else "管理员取消"

    agent = await get_agent(agent_id, chat_key)
    if not agent:
        await finish_with(matcher, message=f"Agent {agent_id} 不存在")
        return

    if not agent.is_active():
        await finish_with(matcher, message=f"Agent {agent_id} 已不在活跃状态 ({agent.status.value})")
        return

    # 取消 Agent
    cancelled = await cancel_agent(agent_id, chat_key, reason)
    if cancelled:
        msg = f"✅ Agent [{agent_id}] 已取消\n原因: {reason}"
        if cancelled.deployed_url:
            msg += f"\n已部署的页面仍可访问: {cancelled.deployed_url}"
        await finish_with(matcher, message=msg)
    else:
        await finish_with(matcher, message=f"❌ 取消 Agent {agent_id} 失败")


@on_command("webapp_history", aliases={"webapp-history", "wa_history", "wa-history"}, priority=5, block=True).handle()
async def _(matcher: Matcher, event: MessageEvent, bot: Bot, arg: Message = CommandArg()):
    """查看历史完成任务"""
    _username, cmd_content, chat_key, _chat_type = await command_guard(event, bot, arg, matcher)

    registry = await get_chat_registry(chat_key)

    if not registry.completed_agents:
        await finish_with(matcher, message="当前会话没有已完成的历史任务")
        return

    # 解析页码
    page = 1
    if cmd_content and cmd_content.strip().isdigit():
        page = int(cmd_content.strip())

    # 按完成时间排序（最新的在前）
    sorted_agents = sorted(
        registry.completed_agents.values(),
        key=lambda x: x.complete_time or 0,
        reverse=True,
    )

    page_size = 5
    total = len(sorted_agents)
    total_pages = (total + page_size - 1) // page_size
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * page_size
    end_idx = min(start_idx + page_size, total)

    lines = [f"📜 历史完成任务 (第 {page}/{total_pages} 页)\n"]

    for agent in sorted_agents[start_idx:end_idx]:
        status_emoji = _get_status_emoji(agent.status)
        html_size = f"{len(agent.current_html)}字符" if agent.current_html else "无"
        lines.append(f"{status_emoji} [{agent.agent_id}] {agent.status.value}")
        lines.append(f"   需求: {agent.requirement[:40]}...")
        lines.append(f"   📄 HTML: {html_size}")
        if agent.deployed_url:
            lines.append(f"   🔗 {agent.deployed_url}")
        lines.append("")

    if total_pages > 1:
        lines.append("使用 webapp-history <页码> 查看其他页")

    await finish_with(matcher, message="\n".join(lines))


@on_command("webapp_clean", aliases={"webapp-clean", "wa_clean", "wa-clean"}, priority=5, block=True).handle()
async def _(matcher: Matcher, event: MessageEvent, bot: Bot, arg: Message = CommandArg()):
    """清理已完成/失败的 Agent"""
    _username, _cmd_content, chat_key, _chat_type = await command_guard(event, bot, arg, matcher)

    cleaned = await clean_completed_agents(chat_key)
    await finish_with(matcher, message=f"🧹 已清理当前会话 {cleaned} 个已完成/失败/取消的 Agent 记录")


@on_command("webapp_help", aliases={"webapp-help", "wa_help", "wa-help"}, priority=5, block=True).handle()
async def _(matcher: Matcher, event: MessageEvent, bot: Bot, arg: Message = CommandArg()):
    """显示帮助信息"""
    await command_guard(event, bot, arg, matcher)

    help_text = """📖 NekroWebApp x SubAgent 命令帮助

🔹 查看命令:
   webapp_list    - 列出当前会话活跃的 Agent
   webapp_info <ID> - 查看指定 Agent 详情
   webapp_stats   - 查看会话统计信息
   webapp_history [页码] - 查看历史完成任务

🔹 管理命令:
   webapp_cancel <ID> [原因] - 取消指定 Agent
   webapp_clean   - 清理已完成的 Agent 记录

🔹 示例:
   webapp_info WEB-a3f8
   webapp_cancel WEB-a3f8 用户取消

🔹 贴士
   1. `webapp` 可简写为 `wa`
   """

    await finish_with(matcher, message=help_text)
