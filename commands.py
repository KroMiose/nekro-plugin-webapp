"""管理员命令

提供管理员用于查看和管理 WebApp Agent 协作系统的命令。
"""

import time
from typing import List

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from nekro_agent.adapters.onebot_v11.matchers.command import (
    command_guard,
    finish_with,
    on_command,
)

from .agent_core import SubAgentStatus
from .models import WebDevAgent
from .services import cancel_agent_task, get_active_agents, get_agent, pool

# ==================== 格式化工具 ====================


STATUS_EMOJI = {
    SubAgentStatus.PENDING: "⏳",
    SubAgentStatus.WORKING: "💻",
    SubAgentStatus.WAITING_INPUT: "💬",
    SubAgentStatus.REVIEWING: "🧐",
    SubAgentStatus.COMPLETED: "✅",
    SubAgentStatus.FAILED: "❌",
    SubAgentStatus.CANCELLED: "🚫",
}

STATUS_COLOR = {
    SubAgentStatus.PENDING: "⚪",
    SubAgentStatus.WORKING: "🔵",
    SubAgentStatus.WAITING_INPUT: "🟡",
    SubAgentStatus.REVIEWING: "🟣",
    SubAgentStatus.COMPLETED: "🟢",
    SubAgentStatus.FAILED: "🔴",
    SubAgentStatus.CANCELLED: "⚫",
}

# ... (omitted)

STATUS_TEXT_CN = {
    SubAgentStatus.PENDING: "待命",
    SubAgentStatus.WORKING: "运行中",
    SubAgentStatus.WAITING_INPUT: "等待用户",
    SubAgentStatus.REVIEWING: "审查中",
    SubAgentStatus.COMPLETED: "已完成",
    SubAgentStatus.FAILED: "失败",
    SubAgentStatus.CANCELLED: "已取消",
}


def _status_emoji(status: SubAgentStatus) -> str:
    return STATUS_EMOJI.get(status, "❓")


def _status_color(status: SubAgentStatus) -> str:
    return STATUS_COLOR.get(status, "⚪")


def _format_time(seconds: int) -> str:
    """格式化耗时"""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60}m"


def _format_chars(chars: int) -> str:
    """格式化字符数"""
    if chars < 1000:
        return f"{chars}"
    if chars < 10000:
        return f"{chars / 1000:.1f}k"
    return f"{chars // 1000}k"


def _calc_stream_speed(agent: WebDevAgent) -> str:
    """计算流式输出速度"""
    if not agent.stream_start_time or agent.stream_chars == 0:
        return "0/s"
    elapsed = time.time() - agent.stream_start_time
    if elapsed < 1:
        return f"{agent.stream_chars}/s"
    speed = agent.stream_chars / elapsed
    return f"{int(speed)}/s"


STATUS_TEXT_CN = {
    SubAgentStatus.PENDING: "待命",
    SubAgentStatus.WORKING: "运行中",
    SubAgentStatus.WAITING_INPUT: "等待用户",
    SubAgentStatus.COMPLETED: "已完成",
    SubAgentStatus.FAILED: "失败",
    SubAgentStatus.CANCELLED: "已取消",
}


def _status_text_cn(status: SubAgentStatus) -> str:
    return STATUS_TEXT_CN.get(status, status.value)


def _calc_auto_progress(agent: WebDevAgent, agent_map: dict) -> tuple[str, int]:
    """自动计算综合进度

    返回：(进度描述, 完成子Agent数/总子Agent数)
    """
    # 终态
    if agent.status == SubAgentStatus.COMPLETED:
        return "✅ 完成", 100
    if agent.status == SubAgentStatus.FAILED:
        return "❌ 失败", 0
    if agent.status == SubAgentStatus.CANCELLED:
        return "🚫 取消", 0
    if agent.status == SubAgentStatus.PENDING:
        return "⏳ 待启动", 0

    # 活跃状态 - 根据输出和子Agent计算
    parts = []

    # 自身输出状态
    if agent.stream_chars > 0:
        speed = _calc_stream_speed(agent)
        parts.append(f"📤 {_format_chars(agent.stream_chars)}字符 ({speed})")
    elif agent.output:
        parts.append(f"📦 {_format_chars(len(str(agent.output)))}字符")
    elif agent.current_html:
        parts.append(f"📄 {_format_chars(len(agent.current_html))}")

    # 子Agent状态
    if agent.children_ids:
        children = [
            agent_map.get(cid) for cid in agent.children_ids if cid in agent_map
        ]
        completed = sum(
            1 for c in children if c and c.status == SubAgentStatus.COMPLETED
        )
        total = len(children)
        parts.append(f"🤖 子任务 {completed}/{total}")

    if agent.status == SubAgentStatus.WAITING_INPUT:
        parts.append("💬 等待反馈")

    return " · ".join(parts) if parts else "🔄 处理中", 0


async def _build_agent_tree(
    agents: List[WebDevAgent],
    verbose: bool = False,
) -> List[str]:
    """构建层级 Agent 树状展示"""
    lines = []

    # 找出根 Agent（无父节点）
    root_agents = [a for a in agents if a.parent_id is None]
    agent_map = {a.agent_id: a for a in agents}

    def render_agent(
        agent: WebDevAgent,
        prefix: str = "",
        is_last: bool = True,
    ) -> None:
        # 连接符 (3字符宽度，确保对齐)
        connector = "└─ " if is_last else "├─ "
        # 子前缀: 如果不是最后一个，需要竖线连接后续节点
        # 竖线在第1位: " │ "
        child_prefix = prefix + ("   " if is_last else " │ ")

        # 状态指示器
        emoji = _status_emoji(agent.status)
        elapsed = _format_time(int(time.time()) - agent.created_at)

        # 进度与速度
        progress_info = ""
        if agent.status == SubAgentStatus.WORKING:
            speed = _calc_stream_speed(agent)
            chars = _format_chars(agent.stream_chars)
            progress_info = f"⚡{speed} · 📝{chars}字"
        elif agent.status == SubAgentStatus.COMPLETED:
            progress_info = f"🏁{_format_chars(agent.total_chars_generated)}字"

        # 角色/层级标识
        # role_tag = f"[{agent.role}]" if agent.role else f"L{agent.level}"
        # 翻译角色
        role_cn = {
            "architect": "架构师",
            "engineer": "工程师",
            "creator": "策划",
            "": "根任务",
        }.get(agent.role, agent.role)
        role_tag = f"[{role_cn}]" if agent.role else f"[Lv.{agent.level}]"

        # 难度星级 (仅 verbose 或 根节点显示)
        diff_star = ""
        if verbose or agent.level == 1:
            diff_star = f" · ⭐{agent.difficulty}"

        # 主行
        # 格式: └─ 🔵 [role] ID · status · info · time
        status_cn = _status_text_cn(agent.status)
        status_line = f"{emoji} {status_cn}"
        if progress_info:
            status_line += f" · {progress_info}"
        status_line += f" · ⏱️{elapsed}{diff_star}"

        lines.append(f"{prefix}{connector}{role_tag} {agent.agent_id}")
        lines.append(f"{child_prefix}   {status_line}")

        # 详细模式
        if verbose:
            lines.append(
                f"{child_prefix}   📝 {agent.task[:40]}{'...' if len(agent.task) > 40 else ''}",
            )
            if agent.current_step:
                lines.append(f"{child_prefix}   👉 {agent.current_step}")
            if agent.deployed_url:
                lines.append(f"{child_prefix}   🔗 {agent.deployed_url}")

        # 子 Agent 统计
        children = [agent_map[cid] for cid in agent.children_ids if cid in agent_map]
        if children and not verbose:  # 简单展示子节点摘要 (如果不是 verbose)
            active_kids = sum(1 for c in children if c.is_active())
            if active_kids > 0:
                lines.append(f"{child_prefix}   🤖 {active_kids} 个活跃子任务")

        # 递归渲染子 Agent
        for i, child in enumerate(children):
            render_agent(child, child_prefix, is_last=(i == len(children) - 1))

    # 渲染所有根 Agent
    for i, root in enumerate(root_agents):
        # 移除了空行，以保持树的连贯性 (特别是当使用了 ├─ 连接符时)
        render_agent(root, "", is_last=(i == len(root_agents) - 1))

    return lines


# ==================== 命令 ====================


@on_command(
    "webapp_list",
    aliases={"webapp-list", "wa_list", "wa-list", "wa_ls", "wa-ls"},
    priority=5,
    block=True,
).handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    bot: Bot,
    arg: Message = CommandArg(),
):
    """列出活跃 Agent

    用法: wa_list [-v|--verbose]
    """
    _, cmd, chat_key, _ = await command_guard(event, bot, arg, matcher)

    verbose = cmd.strip() in ("-v", "--verbose", "-d", "--detail")

    # 获取活跃 Agent
    active_agents = await get_active_agents(chat_key)

    if not active_agents:
        await finish_with(matcher, message="📭 当前会话没有活跃的 Agent")
        return

    # 收集所有相关 Agent（包括已完成的子 Agent）
    all_agents: list[WebDevAgent] = list(active_agents)
    agent_ids = {a.agent_id for a in all_agents}

    # 递归收集每个活跃 Agent 的所有子 Agent
    async def collect_children(agent: WebDevAgent) -> None:
        for child_id in agent.children_ids:
            if child_id not in agent_ids:
                child = await get_agent(child_id, chat_key)
                if child:
                    all_agents.append(child)
                    agent_ids.add(child_id)
                    await collect_children(child)

    for agent in list(active_agents):
        await collect_children(agent)

    agents = all_agents

    # 统计
    working = sum(1 for a in agents if a.status == SubAgentStatus.WORKING)
    waiting = sum(1 for a in agents if a.status == SubAgentStatus.WAITING_INPUT)
    levels = max((a.level for a in agents), default=1)

    header = [
        "🌐 WebApp Agent 协作状态",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 总计 {len(agents)} 个 · 💻运行中 {working} · 💬等待 {waiting} · 🏗️层级 {levels}",
        "",
    ]

    tree = await _build_agent_tree(agents, verbose=verbose)

    footer = [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "💡 提示: wa_list -v 查看详情 · wa_info <ID> 查看单个",
    ]

    await finish_with(matcher, message="\n".join(header + tree + footer))


@on_command(
    "webapp_info",
    aliases={"webapp-info", "wa_info", "wa-info"},
    priority=5,
    block=True,
).handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    bot: Bot,
    arg: Message = CommandArg(),
):
    """查看 Agent 详情

    用法: wa_info <ID> [-v|--verbose]
    """
    _, cmd, chat_key, _ = await command_guard(event, bot, arg, matcher)

    if not cmd:
        await finish_with(matcher, message="❌ 请指定 Agent ID\n用法: wa_info <ID>")
        return

    parts = cmd.strip().split()
    agent_id = parts[0]
    verbose = len(parts) > 1 and parts[1] in ("-v", "--verbose")

    agent = await get_agent(agent_id, chat_key)
    if not agent:
        await finish_with(matcher, message=f"❌ Agent {agent_id} 不存在")
        return

    emoji = _status_emoji(agent.status)
    elapsed = _format_time(int(time.time()) - agent.created_at)

    # 流式统计
    stream_info = "等待输出..."
    if agent.stream_chars > 0:
        speed = _calc_stream_speed(agent)
        stream_info = f"{_format_chars(agent.stream_chars)} 字 ({speed})"
    elif agent.output:
        stream_info = f"📦 已产出 {_format_chars(len(str(agent.output)))} 字"

    # 进度条
    progress_bar = "█" * (agent.progress // 10) + "░" * (10 - agent.progress // 10)

    # 角色汉化
    role_cn = {
        "architect": "架构师",
        "engineer": "工程师",
        "creator": "策划",
        "": "根任务",
    }.get(agent.role, agent.role)

    lines = [
        f"🌐 Agent [{agent.agent_id}]",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{emoji} 状态: {_status_text_cn(agent.status)} ({agent.progress}%)",
        f"📊 进度: {progress_bar}",
        f"⚡ 输出: {stream_info}",
        f"🏗️ 角色: {role_cn} (Lv.{agent.level})",
        f"⏱️ 耗时: {elapsed}",
        f"🎯 难度: {'⭐' * agent.difficulty}{'☆' * (5 - agent.difficulty)} ({agent.difficulty}/5)",
    ]

    if agent.current_step:
        lines.append(f"🔸 当前: {agent.current_step}")

    lines.append("")
    lines.append(f"📝 任务: {agent.task[:60]}{'...' if len(agent.task) > 60 else ''}")

    # 完整任务树视图
    lines.append("")
    lines.append("🌳 任务树视图:")

    # 找到根 Agent
    root_agent = agent
    all_agents = {agent.agent_id: agent}

    # 向上找根
    current = agent
    while current.parent_id:
        parent = await get_agent(current.parent_id, chat_key)
        if parent:
            all_agents[parent.agent_id] = parent
            root_agent = parent
            current = parent
        else:
            break

    # 递归收集所有子 Agent
    async def collect_all_children(a: WebDevAgent) -> None:
        for child_id in a.children_ids:
            if child_id not in all_agents:
                child = await get_agent(child_id, chat_key)
                if child:
                    all_agents[child_id] = child
                    await collect_all_children(child)

    for a in list(all_agents.values()):
        await collect_all_children(a)

    # 渲染树
    def render_tree_node(
        a: WebDevAgent,
        prefix: str = "",
        is_last: bool = True,
    ) -> None:
        connector = "└─" if is_last else "├─"
        marker = "👉" if a.agent_id == agent.agent_id else "  "  # 标记当前 Agent
        status_emoji = _status_emoji(a.status)
        role_name = {
            "architect": "架构师",
            "engineer": "工程师",
            "creator": "策划",
            "": "根任务",
        }.get(a.role, a.role)
        role_tag = f"[{role_name}]" if a.role else f"[Lv.{a.level}]"
        lines.append(
            f"{prefix}{connector}{marker}{status_emoji} {role_tag} {a.agent_id}",
        )

        child_prefix = prefix + ("   " if is_last else "│  ")
        children = [all_agents[cid] for cid in a.children_ids if cid in all_agents]
        for i, child in enumerate(children):
            render_tree_node(child, child_prefix, is_last=(i == len(children) - 1))

    render_tree_node(root_agent, "  ")

    # 产物
    if agent.current_html or agent.deployed_url or agent.child_outputs:
        lines.append("")
        if agent.current_html:
            lines.append(f"📄 HTML: {len(agent.current_html)} 字符")
        if agent.template:
            lines.append(f"📋 模板: {len(agent.template)} 字符")
        if agent.child_outputs:
            lines.append(f"📦 子产物: {', '.join(agent.child_outputs.keys())}")
        if agent.deployed_url:
            lines.append(f"🔗 {agent.deployed_url}")

    # 详细模式
    if verbose:
        lines.append("")
        lines.append(f"📨 消息记录: {len(agent.messages)} 条")
        for msg in agent.messages[-3:]:
            time_str = time.strftime("%H:%M", time.localtime(msg.timestamp))
            sender = "⬆️" if msg.sender in ("main", "parent") else "⬇️"
            content = msg.content[:30] + "..." if len(msg.content) > 30 else msg.content
            lines.append(f"  {sender}[{time_str}] {content}")

    if agent.error_message:
        lines.append("")
        lines.append(f"❌ 错误: {agent.error_message[:50]}")

    await finish_with(matcher, message="\n".join(lines))


@on_command(
    "webapp_cancel",
    aliases={"webapp-cancel", "wa_cancel", "wa-cancel"},
    priority=5,
    block=True,
).handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    bot: Bot,
    arg: Message = CommandArg(),
):
    """取消 Agent"""
    _, cmd, chat_key, _ = await command_guard(event, bot, arg, matcher)

    if not cmd:
        await finish_with(
            matcher,
            message="❌ 请指定 Agent ID\n用法: wa_cancel <ID> [原因]",
        )
        return

    parts = cmd.strip().split(maxsplit=1)
    agent_id = parts[0]
    reason = parts[1] if len(parts) > 1 else "管理员取消"

    agent = await get_agent(agent_id, chat_key)
    if not agent:
        await finish_with(matcher, message=f"❌ Agent {agent_id} 不存在")
        return

    if not agent.is_active():
        await finish_with(
            matcher,
            message=f"⚠️ Agent {agent_id} 已结束 ({agent.status.value})",
        )
        return

    # 取消该 Agent 及其所有子 Agent
    cancelled = [agent_id]
    for child_id in agent.children_ids:
        await cancel_agent_task(child_id, chat_key, "父Agent取消")
        cancelled.append(child_id)

    await cancel_agent_task(agent_id, chat_key, reason)

    msg_lines = [
        f"✅ 已取消 {len(cancelled)} 个 Agent",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🚫 {' → '.join(cancelled)}",
        f"📝 原因: {reason}",
    ]
    if agent.deployed_url:
        msg_lines.append(f"🔗 页面仍可访问: {agent.deployed_url}")

    await finish_with(matcher, message="\n".join(msg_lines))


@on_command(
    "webapp_recompile",
    aliases={"webapp-recompile", "wa_recompile", "wa-recompile", "wa_build"},
    priority=5,
    block=True,
).handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    bot: Bot,
    arg: Message = CommandArg(),
):
    """(高级) 重新编译 Agent"""
    _, cmd, chat_key, _ = await command_guard(event, bot, arg, matcher)

    if not cmd:
        await finish_with(
            matcher,
            message="❌ 请指定 Agent ID\n用法: wa_recompile <ID>",
        )
        return

    agent_id = cmd.strip()
    await matcher.send(f"🔨 正在重新编译 Agent[{agent_id}] 产物...")

    from .services.agent_runner import recompile_agent

    result = await recompile_agent(agent_id, chat_key)
    await finish_with(matcher, message=result)


@on_command(
    "webapp_help",
    aliases={"webapp-help", "wa_help", "wa-help"},
    priority=5,
    block=True,
).handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    bot: Bot,
    arg: Message = CommandArg(),
):
    """帮助"""
    _, cmd, _, _ = await command_guard(event, bot, arg, matcher)

    show_advanced = cmd.strip() in ("-v", "--verbose", "--advanced", "-a")

    msg = """🌐 WebApp 命令帮助

📋 查看
  wa_list → 列出活跃 Agent
  wa_list -v → 详细树状展示
  wa_info <ID> → 查看单个详情
  wa_info <ID> -v → 完整信息

⚙️ 管理
  wa_cancel <ID> [原因]
  取消 Agent 及其所有子 Agent

🎨 状态图例
  🔵 运行中  🟡 等待用户
  🟢 已完成  🔴 失败
  ⚪ 待命    ⚫ 已取消"""

    if show_advanced:
        msg += """

🔧 高级命令
  wa_recompile <ID>
  手动触发重新编译和部署 (仅限根节点)"""
    await finish_with(matcher, message=msg)


@on_command(
    "webapp_deps",
    aliases={"webapp-deps", "wa_deps", "wa-deps"},
    priority=5,
    block=True,
).handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    bot: Bot,
    arg: Message = CommandArg(),
):
    """查看缺失依赖统计

    用法: wa_deps [-p <page>] [-s <size>]
    """
    _, cmd, _, _ = await command_guard(event, bot, arg, matcher)

    # 简单参数解析
    page = 1
    page_size = 10

    parts = cmd.strip().split()
    i = 0
    while i < len(parts):
        val = parts[i]
        if val in ("-p", "--page") and i + 1 < len(parts):
            try:
                page = int(parts[i + 1])
                i += 2
                continue
            except ValueError:
                pass
        if val in ("-s", "--size") and i + 1 < len(parts):
            try:
                page_size = int(parts[i + 1])
                i += 2
                continue
            except ValueError:
                pass
        i += 1

    import json

    from .plugin import plugin

    store_key = "global_missing_dependencies"
    data = await plugin.store.get(store_key=store_key)

    if not data:
        await finish_with(matcher, message="📭 当前没有记录到任何缺失的依赖。")
        return

    try:
        loaded = json.loads(data)
        if not loaded:
            await finish_with(matcher, message="📭 当前没有记录到任何缺失的依赖。")
            return

        # 兼容旧列表格式
        deps_dict = {}
        if isinstance(loaded, list):
            deps_dict = dict.fromkeys(loaded, 1)
        elif isinstance(loaded, dict):
            deps_dict = loaded

        # 排序: 次数倒序
        sorted_deps = sorted(deps_dict.items(), key=lambda x: x[1], reverse=True)

        # 分页
        total = len(sorted_deps)
        total_pages = (total + page_size - 1) // page_size
        page = max(1, min(page, total_pages))

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_items = sorted_deps[start_idx:end_idx]

        if not page_items:
            await finish_with(
                matcher, message=f"⚠️ 第 {page} 页没有数据 (总共 {total} 条记录)",
            )
            return

        lines = [
            "📊 缺失依赖统计",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"总计: {total} 个 · 页码: {page}/{total_pages}",
            "",
        ]

        for idx, (dep, count) in enumerate(page_items, start_idx + 1):
            lines.append(f"{idx}. {dep} (失败 {count} 次)")

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        if page < total_pages:
            lines.append(f"💡 下一页: wa_deps -p {page + 1}")

        await finish_with(matcher, message="\n".join(lines))
    except Exception as e:
        await finish_with(matcher, message=f"❌ 读取记录失败: {e}")
