"""Common utilities for building agent prompts"""

import time
from typing import TYPE_CHECKING, List

from nekro_agent.services.agent.creator import OpenAIChatMessage

from ..agent_core import SubAgentStatus
from ..plugin import config
from ..services import vfs

if TYPE_CHECKING:
    from ..models import WebDevAgent

import re


def build_file_tree_section(agent: "WebDevAgent") -> str:
    """构建当前项目文件树预览，标识文件归属和导出信息（基于 VFS 所有权）"""
    project_ctx = vfs.get_project_context(agent.chat_key)
    files = sorted(project_ctx.list_files())

    if not files:
        return ""

    # 确定当前 Agent 拥有的文件（优先使用 VFS 所有权记录）
    owned_files: set[str] = set()

    for f in files:
        owner = project_ctx.get_owner(f)
        if owner == agent.agent_id:
            owned_files.add(f)
        elif owner is None:
            # 无 owner 时，使用传统逻辑作为回退
            # 🔄 新逻辑：根 Agent（无论 level）拥有核心文件
            if agent.is_root():
                core_patterns = ["src/main.tsx", "src/App.tsx", "src/index.css"]
                if f in core_patterns or f.startswith("src/types/"):
                    owned_files.add(f)
            else:  # 子 Agent：从 task 描述中提取被指派的文件
                task_text = agent.spec.task if agent.spec else agent.task
                path_matches = re.findall(r"src/[\w/\-\.]+\.\w+", task_text)
                if f in path_matches:
                    owned_files.add(f)

    # 构建文件树（对于非自己的文件，显示导出信息帮助正确导入）
    tree = "\n## 📁 Current Project Files\n\n```\n"
    for f in files:
        size = len(project_ctx.files.get(f, ""))
        owner = project_ctx.get_owner(f)

        # 提取导出信息（仅对非自己的 .ts/.tsx 文件）
        exports_hint = ""
        if f.endswith((".ts", ".tsx")):
            exports = project_ctx.extract_exports(f)
            if exports:
                # 限制显示数量，避免过长
                display_exports = exports[:5]
                exports_str = ", ".join(display_exports)
                if len(exports) > 5:
                    exports_str += f" (+{len(exports) - 5} more)"
                exports_hint = f"\n     └─ exports: {exports_str}"

        if f in owned_files:
            tree += f"  ✅ {f} ({size} chars) [YOUR FILE]\n"
        elif owner:
            tree += f"  🔒 {f} ({size} chars) [Owner: {owner}]{exports_hint}\n"
        else:
            tree += f"  📄 {f} ({size} chars) [Unassigned]\n"
    tree += "```\n"

    if owned_files:
        tree += f"\n**Your files**: {', '.join(sorted(owned_files))}\n"
    tree += "**🚫 只能修改标记为 ✅ 的文件。使用 `<transfer_ownership>` 可转让其他文件的所有权。**\n"
    return tree


def build_reusable_agents_section(
    agent: "WebDevAgent",
    all_agents: dict[str, "WebDevAgent"] | None = None,
) -> str:
    """构建可复用 Agent 列表及正在工作的 Agent 状态

    Args:
        agent: 当前 Agent
        all_agents: 预加载的所有 Agent 字典（从 pool.load 获取）
                   如果为 None，则尝试同步加载（在异步环境中会失败）
    """
    if all_agents is None:
        # 回退到同步加载（在异步环境中会失败）
        import asyncio

        from ..services import pool

        async def _get_all():
            return await pool.load(agent.chat_key)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return ""  # 异步环境中无法同步加载
            all_agents = loop.run_until_complete(_get_all())
        except Exception:
            return ""

    if not all_agents:
        return ""

    # 分类 Agent
    working_agents = []
    reusable_agents = []

    for a in all_agents.values():
        if a.agent_id == agent.agent_id:
            continue
        if a.status == SubAgentStatus.WORKING:
            working_agents.append(a)
        elif a.status in (
            SubAgentStatus.COMPLETED,
            SubAgentStatus.FAILED,
        ) and a.role in ("engineer", "creator"):
            reusable_agents.append(a)

    section = ""

    # 🔄 新逻辑：显示当前 Agent 的直接子 Agent（正在工作的）
    # 基于 parent_id 过滤，而非 level
    my_working_children = [a for a in working_agents if a.parent_id == agent.agent_id]

    if my_working_children:
        section += "\n## ⏳ Active Workers (Your Sub-Agents)\n\n"
        section += "These are YOUR sub-agents currently working. Wait for them to complete before spawning duplicate tasks.\n\n"
        section += "| Agent ID | Role | Task | Progress |\n"
        section += "|----------|------|------|----------|\n"
        for a in my_working_children[:8]:
            task_preview = a.task[:35] + "..." if len(a.task) > 35 else a.task
            section += f"| {a.agent_id} | {a.role} | {task_preview} | {a.progress}% |\n"
        section += "\n"

    # 显示可复用的 Agent
    if reusable_agents:
        section += "\n## 🔄 Reusable Agents (Completed)\n\n"
        section += "These agents have finished their tasks but can be **reactivated** with new tasks using `reuse: <agent_id>`.\n"
        section += "They retain context from their previous work. **Use `reuse` to fix files they own!**\n\n"
        section += "| Agent ID | Role | Status | Last Task | Owned Files |\n"
        section += "|----------|------|--------|-----------|-------------|\n"

        from ..services import vfs

        project_ctx = vfs.get_project_context(agent.chat_key)

        for a in reusable_agents[:8]:
            task_preview = a.task[:30] + "..." if len(a.task) > 30 else a.task
            status_icon = "✅" if a.status == SubAgentStatus.COMPLETED else "❌"

            # 查找该 Agent 拥有的文件
            owned = [
                f
                for f in project_ctx.list_files()
                if project_ctx.get_owner(f) == a.agent_id
            ]
            owned_str = ", ".join(owned[:3]) if owned else "-"
            if len(owned) > 3:
                owned_str += f" (+{len(owned) - 3})"

            section += f"| {a.agent_id} | {a.role} | {status_icon} | {task_preview} | {owned_str} |\n"

    return section


def build_identity_section(agent: "WebDevAgent") -> str:
    """构建身份信息部分"""
    return f"""# Identity: {agent.role or "WebDev Agent"} [{agent.agent_id}]

- Level: L{agent.level}
- Status: {agent.status.value}
- Progress: {agent.progress}%
"""


def build_messages_history(agent: "WebDevAgent") -> str:
    """构建通信历史部分"""
    if not agent.messages:
        return ""

    history = "\n## Communication History\n\n```\n"
    for msg in agent.messages[-10:]:
        time_str = time.strftime("%H:%M:%S", time.localtime(msg.timestamp))
        sender = "Superior" if msg.sender in ("main", "parent") else "Me"
        history += f"[{time_str}] {sender}: {msg.content[:100]}...\n"
    history += "```\n"
    return history


def build_common_messages(
    agent: "WebDevAgent",
    system_prompt: str,
) -> List[OpenAIChatMessage]:
    """构建通用消息列表"""
    messages: List[OpenAIChatMessage] = []

    # System Prompt
    messages.append(
        OpenAIChatMessage.from_text("system", system_prompt),
    )

    # Initial Task
    task_text = agent.task
    if agent.spec:
        task_text = f"[Task assigned by Superior]\n\n{agent.spec.task}\n\nExpected Output: {agent.spec.output_format}"

    messages.append(
        OpenAIChatMessage.from_text("user", f"[Mission Start]\n\n{task_text}"),
    )

    # History - 构建原始消息列表
    raw_messages: List[OpenAIChatMessage] = []
    for i, msg in enumerate(agent.messages):
        if i == 0 and msg.msg_type == "instruction":
            continue

        if msg.sender in ("main", "parent", "system"):
            prefix = {
                "instruction": "[Instruction]",
                "feedback": "[Feedback]",
                "error": "[System Error]",
            }.get(msg.msg_type, "[System Message]")

            raw_messages.append(
                OpenAIChatMessage.from_text("user", f"{prefix} {msg.content}"),
            )
        else:
            raw_messages.append(OpenAIChatMessage.from_text("assistant", msg.content))

    # 合并连续的 user 消息
    for msg in raw_messages:
        if msg.role == "user" and messages and messages[-1].role == "user":
            # 合并到上一条 user 消息（使用 extend 方法）
            messages[-1] = messages[-1].extend(msg)
        else:
            messages.append(msg)

    # Continue prompt
    if agent.status == SubAgentStatus.WORKING:
        # 检查最后一条是否是 user，如果是则合并
        if messages and messages[-1].role == "user":
            proceed_msg = OpenAIChatMessage.from_text("user", "\n\nProceed.")
            messages[-1] = messages[-1].extend(proceed_msg)
        else:
            messages.append(
                OpenAIChatMessage.from_text("user", "Proceed."),
            )

    return messages
