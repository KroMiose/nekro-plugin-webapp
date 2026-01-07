"""子 Agent 系统提示词

构建子 Agent 的系统提示词和消息历史。
"""

import time
from typing import List

from nekro_agent.services.agent.creator import OpenAIChatMessage

from ..models import AgentStatus, MessageType, WebDevAgent
from ..plugin import config


def build_webdev_system_prompt(agent: WebDevAgent) -> str:
    """构建子 Agent 的系统提示词

    Args:
        agent: Agent 实例

    Returns:
        系统提示词
    """
    # 格式化通信历史
    messages_history = ""
    if agent.messages:
        messages_history = "\n## 与主 Agent 的沟通记录\n```\n"
        for msg in agent.messages[-10:]:  # 最近 10 条
            time_str = time.strftime("%H:%M:%S", time.localtime(msg.timestamp))
            sender = "主Agent" if msg.sender == "main" else "我"
            messages_history += (
                f"[{time_str}] {sender} ({msg.msg_type.value}): {msg.content}\n"
            )
        messages_history += "```\n"

    # 当前 HTML 状态
    html_status = ""
    if agent.current_html:
        max_len = config.HTML_PREVIEW_LENGTH
        if len(agent.current_html) > max_len:
            html_preview = (
                agent.current_html[:max_len]
                + f"\n\n... (共 {len(agent.current_html)} 字符，已截断)"
            )
        else:
            html_preview = agent.current_html
        html_status = f"""
## 当前代码状态
已完成的 HTML 代码 ({len(agent.current_html)} 字符):
```html
{html_preview}
```
"""

    # 模板变量概览
    template_vars_section = ""
    if agent.template_vars:
        template_vars_section = "\n## 📦 可用模板变量\n\n"
        template_vars_section += "主 Agent 提供了以下模板变量，你可以在 HTML 中使用 `{{变量名}}` 占位符引用：\n\n"
        template_vars_section += "| 变量名 | 内容预览 |\n|--------|----------|\n"
        for key, preview in agent.get_all_template_previews(
            config.TEMPLATE_VAR_PREVIEW_LEN,
        ).items():
            # 转义 Markdown 表格特殊字符
            safe_preview = preview.replace("|", "\\|").replace("\n", " ")[:100]
            template_vars_section += f"| `{key}` | {safe_preview} |\n"
        template_vars_section += "\n**使用方式**: 在 HTML 中写入 `{{变量名}}`，部署时会自动替换为实际内容。\n"
        template_vars_section += '**示例**: `<img src="{{logo_base64}}" alt="Logo">` 或 `<p>{{intro_text}}</p>`\n'

    return f"""# 你是 WebDev Agent [{agent.agent_id}]

你是一个专业的网页开发 Agent，隶属于 NekroAgent 系统。你的职责是根据主 Agent 转达的用户需求，独立完成网页开发任务。

## 你的身份

- Agent ID: {agent.agent_id}
- 当前状态: {agent.status.value}
- 进度: {agent.progress_percent}%
- 迭代次数: {agent.iteration_count}

## 当前任务

**原始需求:**
> {agent.requirement}

**任务概要:**
{agent.task_summary or "(待分析)"}

**当前步骤:**
{agent.current_step or "(待开始)"}
{messages_history}{html_status}{template_vars_section}
## 你的能力和规范

### 1. 状态更新

每次回复必须包含状态更新块:

```
<status>
progress: 65
step: "正在编写响应式布局CSS"
</status>
```

### 2. 与主 Agent 沟通

当需要询问、汇报进度或提交成果时:

```
<message type="question|progress|result">
你想说的内容
</message>
```

消息类型说明:
- question: 有疑问需要主 Agent 确认或回答
- progress: 汇报当前工作进度
- result: 提交最终成果

### 3. 代码输出

**首次创建** - 输出完整 HTML:

```
<code>
<!-- TITLE: 页面标题 -->
<!-- DESC: 页面描述 -->
<!DOCTYPE html>
<html>
...完整HTML代码...
</html>
</code>
```

**迭代修改** - 使用增量编辑（推荐，更高效）:

通过 CSS 选择器精确修改 DOM 节点，无需输出完整代码：

```
<edit selector=".header h1">新的标题内容</edit>

<edit selector="#main-content .intro" action="replace">
<p class="intro">替换后的完整元素</p>
</edit>

<edit selector=".container" action="append">
<div class="new-section">追加的新元素</div>
</edit>
```

支持的 action：
- `replace`（默认）：替换节点内容
- `append`：在节点末尾追加子元素
- `prepend`：在节点开头插入子元素
- `remove`：删除匹配的节点

**注意**: 如果需要大范围重构，仍可使用 `<code>` 输出完整代码。

## 工作流程

1. 分析需求，明确要实现的功能点
2. 如有不清楚的地方，通过 <message type="question"> 询问主 Agent
3. 逐步编写代码，定期更新进度
4. 完成后提交成果，等待主 Agent 确认或反馈
5. 根据反馈进行修改迭代

## 代码质量要求

- 现代美观的 UI 设计
- 完整的响应式布局
- 合适的移动端和 PC 端适配
- 使用 CSS 变量实现主题
- 适当的动画效果
- 无外部依赖，单文件完整运行
- 语义化 HTML 结构
- 注重用户体验和视觉美感

## 重要提醒

- 每次回复都要更新 <status> 块
- 代码必须是完整的、可独立运行的 HTML
- 如果主 Agent 提出修改意见，在原有代码基础上修改，保持整体结构
- 对于不确定的设计细节，主动询问而不是自行决定
"""


def build_webdev_messages(agent: WebDevAgent) -> List[OpenAIChatMessage]:
    """构建子 Agent 的完整消息历史

    Args:
        agent: Agent 实例

    Returns:
        消息列表
    """
    messages: List[OpenAIChatMessage] = []

    # 1. 系统提示词
    system_prompt = build_webdev_system_prompt(agent)
    messages.append(OpenAIChatMessage.from_text("system", system_prompt))

    # 2. 初始任务作为第一条 user 消息
    messages.append(
        OpenAIChatMessage.from_text(
            "user",
            f"[任务开始] 请分析以下需求并开始开发:\n\n{agent.requirement}",
        ),
    )

    # 3. 历史对话 (跳过初始的 INSTRUCTION)
    for i, msg in enumerate(agent.messages):
        if i == 0 and msg.msg_type == MessageType.INSTRUCTION:
            continue  # 初始需求已作为第一条消息

        if msg.sender == "main":
            # 主 Agent 的消息
            prefix = {
                MessageType.INSTRUCTION: "[新指令]",
                MessageType.FEEDBACK: "[修改反馈]",
                MessageType.ANSWER: "[回答你的问题]",
            }.get(msg.msg_type, "[消息]")
            messages.append(
                OpenAIChatMessage.from_text("user", f"{prefix} {msg.content}"),
            )
        else:
            # 自己的历史回复
            messages.append(OpenAIChatMessage.from_text("assistant", msg.content))

    # 4. 继续工作提示 (如果是被唤醒继续工作)
    if agent.status in [AgentStatus.THINKING, AgentStatus.CODING]:
        messages.append(
            OpenAIChatMessage.from_text(
                "user",
                "[系统] 请继续你的工作，记得更新状态和进度。",
            ),
        )

    return messages
