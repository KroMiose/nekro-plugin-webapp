"""数据模型定义"""

import os
import time
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ==================== API 相关模型 ====================


class CreatePageRequest(BaseModel):
    """创建页面请求"""

    title: str = Field(..., min_length=1, max_length=200, description="页面标题")
    description: str = Field(..., min_length=1, max_length=1000, description="页面描述")
    html_content: str = Field(..., min_length=1, description="HTML 内容")
    expires_in_days: int = Field(default=30, ge=0, description="过期天数（0=永久）")


class CreatePageResponse(BaseModel):
    """创建页面响应"""

    page_id: str = Field(..., description="页面 ID")
    url: str = Field(..., description="访问 URL")
    title: str = Field(..., description="页面标题")
    created_at: int = Field(..., description="创建时间戳")
    expires_at: int | None = Field(default=None, description="过期时间戳")


class PageInfo(BaseModel):
    """页面信息"""

    page_id: str = Field(..., description="页面 ID")
    title: str = Field(..., description="页面标题")
    description: str = Field(..., description="页面描述")
    created_at: int = Field(..., description="创建时间戳")
    expires_at: int | None = Field(default=None, description="过期时间戳")
    access_count: int = Field(default=0, description="访问次数")
    is_active: bool = Field(default=True, description="是否活跃")


class ApiKeyInfo(BaseModel):
    """API 密钥信息"""

    key_id: str = Field(..., description="密钥 ID")
    key_name: str = Field(..., description="密钥名称")
    created_at: int = Field(..., description="创建时间戳")
    expires_at: int | None = Field(default=None, description="过期时间戳")
    is_active: bool = Field(default=True, description="是否活跃")
    usage_count: int = Field(default=0, description="使用次数")
    max_pages: int = Field(default=100, description="最大页面数")
    permissions: str = Field(default="create,view", description="权限列表")


class WorkerHealthResponse(BaseModel):
    """Worker 健康检查响应"""

    status: str = Field(..., description="状态")
    timestamp: int = Field(..., description="时间戳")
    initialized: bool = Field(default=False, description="是否已初始化管理密钥")


class WorkerStats(BaseModel):
    """Worker 统计信息"""

    pages_count: int = Field(default=0, description="页面总数")
    keys_count: int = Field(default=0, description="密钥总数")
    total_access: int = Field(default=0, description="总访问次数")


# ==================== 多 Agent 系统模型 ====================


class AgentStatus(str, Enum):
    """子 Agent 状态"""

    PENDING = "等待启动"
    THINKING = "分析需求"
    CODING = "编码中"
    DEPLOYING = "部署中"
    WAITING_FEEDBACK = "等待反馈"
    WAITING_CONFIRM = "待确认"  # 用户已确认完成，但尚未归档
    COMPLETED = "已完成"
    FAILED = "失败"
    CANCELLED = "已取消"


class MessageType(str, Enum):
    """消息类型"""

    INSTRUCTION = "指令"  # 主Agent发送的指令/需求
    FEEDBACK = "反馈"  # 主Agent发送的修改意见
    QUESTION = "询问"  # 子Agent向主Agent提问
    ANSWER = "回答"  # 主Agent回答子Agent的问题
    PROGRESS = "进度"  # 子Agent汇报进度
    RESULT = "成果"  # 子Agent提交成果


class AgentMessage(BaseModel):
    """Agent 间通信消息"""

    msg_id: str = Field(..., description="消息唯一 ID")
    msg_type: MessageType = Field(..., description="消息类型")
    sender: Literal["main", "webdev"] = Field(..., description="发送方")
    content: str = Field(..., description="消息内容")
    timestamp: int = Field(..., description="时间戳")
    attachments: Optional[Dict[str, Any]] = Field(default=None, description="附加数据")

    @classmethod
    def create(
        cls,
        msg_type: MessageType,
        sender: Literal["main", "webdev"],
        content: str,
        attachments: Optional[Dict[str, Any]] = None,
    ) -> "AgentMessage":
        """创建消息"""
        return cls(
            msg_id=os.urandom(4).hex(),
            msg_type=msg_type,
            sender=sender,
            content=content,
            timestamp=int(time.time()),
            attachments=attachments,
        )


class WebDevAgent(BaseModel):
    """子 Agent 实例"""

    agent_id: str = Field(..., description="唯一标识，如 WEB-a3f8")
    chat_key: str = Field(..., description="所属会话")
    status: AgentStatus = Field(default=AgentStatus.PENDING, description="当前状态")

    # 任务信息
    requirement: str = Field(..., description="原始需求")
    task_summary: str = Field(default="", description="AI 总结的任务概要")
    difficulty: int = Field(default=5, ge=1, le=10, description="任务难度评分 (1-10)")

    # 进度追踪
    progress_percent: int = Field(default=0, ge=0, le=100, description="进度百分比")
    current_step: str = Field(default="", description="当前步骤描述")

    # 工作产出
    current_html: Optional[str] = Field(default=None, description="当前 HTML 内容")
    deployed_url: Optional[str] = Field(default=None, description="已部署的 URL")
    page_title: Optional[str] = Field(default=None, description="页面标题")
    page_description: Optional[str] = Field(default=None, description="页面描述")

    # 时间追踪
    create_time: int = Field(..., description="创建时间")
    start_time: Optional[int] = Field(default=None, description="开始工作时间")
    last_active_time: int = Field(..., description="最后活跃时间")
    last_access_time: int = Field(..., description="最后访问时间（用于自动归档判断）")
    confirmed_time: Optional[int] = Field(default=None, description="确认完成时间")
    complete_time: Optional[int] = Field(default=None, description="完成/归档时间")

    # 通信记录
    messages: List[AgentMessage] = Field(default_factory=list, description="通信历史")

    # 迭代控制
    iteration_count: int = Field(default=0, description="迭代次数")
    error_message: Optional[str] = Field(default=None, description="错误信息")

    # 模板变量 (key -> value)
    template_vars: Dict[str, str] = Field(
        default_factory=dict,
        description="模板变量，子 Agent 可通过 {{key}} 引用",
    )

    @classmethod
    def create(
        cls,
        agent_id: str,
        chat_key: str,
        requirement: str,
        difficulty: int = 5,
    ) -> "WebDevAgent":
        """创建新的 Agent 实例"""
        current_time = int(time.time())
        return cls(
            agent_id=agent_id,
            chat_key=chat_key,
            requirement=requirement,
            difficulty=difficulty,
            status=AgentStatus.PENDING,
            create_time=current_time,
            last_active_time=current_time,
            last_access_time=current_time,
        )

    def touch(self) -> None:
        """更新访问时间"""
        self.last_access_time = int(time.time())

    def confirm(self) -> None:
        """标记为已确认（等待归档）"""
        self.status = AgentStatus.WAITING_CONFIRM
        self.confirmed_time = int(time.time())
        self.last_access_time = int(time.time())

    def should_auto_archive(self, auto_archive_minutes: int) -> bool:
        """检查是否应该自动归档"""
        if self.status != AgentStatus.WAITING_CONFIRM:
            return False
        elapsed_minutes = (int(time.time()) - self.last_access_time) / 60
        return elapsed_minutes >= auto_archive_minutes

    def is_timeout(self, timeout_minutes: int) -> bool:
        """检查是否超时（长时间等待反馈）

        对于 WAITING_FEEDBACK 状态的 Agent，如果超过指定时间无响应，视为超时。

        Args:
            timeout_minutes: 超时时间（分钟）

        Returns:
            是否超时
        """
        if self.status != AgentStatus.WAITING_FEEDBACK:
            return False
        elapsed_minutes = (int(time.time()) - self.last_active_time) / 60
        return elapsed_minutes >= timeout_minutes

    def add_message(
        self,
        msg_type: MessageType,
        sender: Literal["main", "webdev"],
        content: str,
        attachments: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        """添加消息到通信记录"""
        msg = AgentMessage.create(msg_type, sender, content, attachments)
        self.messages.append(msg)
        self.last_active_time = int(time.time())
        return msg

    def update_progress(self, percent: int, step: str) -> None:
        """更新进度"""
        self.progress_percent = max(0, min(100, percent))
        self.current_step = step
        self.last_active_time = int(time.time())

    def update_status(self, status: AgentStatus) -> None:
        """更新状态"""
        self.status = status
        self.last_active_time = int(time.time())
        if status == AgentStatus.COMPLETED:
            self.complete_time = int(time.time())
            self.progress_percent = 100

    def is_active(self) -> bool:
        """判断是否为活跃状态（包括待确认状态）"""
        return self.status not in [
            AgentStatus.COMPLETED,
            AgentStatus.FAILED,
            AgentStatus.CANCELLED,
        ]

    def is_working(self) -> bool:
        """判断是否正在工作（不包括待确认状态）"""
        return self.status not in [
            AgentStatus.COMPLETED,
            AgentStatus.FAILED,
            AgentStatus.CANCELLED,
            AgentStatus.WAITING_CONFIRM,
        ]

    def set_template_var(self, key: str, value: str) -> None:
        """设置模板变量"""
        self.template_vars[key] = value
        self.last_active_time = int(time.time())

    def delete_template_var(self, key: str) -> bool:
        """删除模板变量"""
        if key in self.template_vars:
            del self.template_vars[key]
            self.last_active_time = int(time.time())
            return True
        return False

    def get_template_var_preview(self, key: str, preview_len: int = 50) -> str:
        """获取模板变量的预览（前后各 preview_len 个字符）"""
        value = self.template_vars.get(key, "")
        if not value:
            return "(空)"
        if len(value) <= preview_len * 2:
            return value
        return f"{value[:preview_len]}...({len(value)} 字符)...{value[-preview_len:]}"

    def get_all_template_previews(self, preview_len: int = 50) -> Dict[str, str]:
        """获取所有模板变量的预览"""
        return {key: self.get_template_var_preview(key, preview_len) for key in self.template_vars}

    def render_html(self, html_content: str) -> str:
        """渲染 HTML，替换模板变量占位符"""
        result = html_content
        for key, value in self.template_vars.items():
            placeholder = "{{" + key + "}}"
            result = result.replace(placeholder, value)
        return result


class ChatAgentRegistry(BaseModel):
    """会话级 Agent 注册表 - 同一 chat_key 下的所有 Agent"""

    active_agents: Dict[str, WebDevAgent] = Field(
        default_factory=dict,
        description="活跃的 Agent，key 为 agent_id",
    )
    completed_agents: List[str] = Field(
        default_factory=list,
        description="已完成的 Agent ID 列表（保留最近 N 个）",
    )

    def add_agent(self, agent: WebDevAgent) -> None:
        """添加 Agent"""
        self.active_agents[agent.agent_id] = agent

    def get_agent(self, agent_id: str) -> Optional[WebDevAgent]:
        """获取 Agent"""
        return self.active_agents.get(agent_id)

    def remove_agent(self, agent_id: str) -> Optional[WebDevAgent]:
        """移除 Agent（不归档）"""
        return self.active_agents.pop(agent_id, None)

    def archive_agent(self, agent_id: str, max_history: int = 10) -> Optional[WebDevAgent]:
        """归档 Agent（从活跃列表移到已完成列表）"""
        agent = self.active_agents.pop(agent_id, None)
        if agent:
            self.completed_agents.append(agent_id)
            # 保留最近 N 个
            if len(self.completed_agents) > max_history:
                self.completed_agents = self.completed_agents[-max_history:]
        return agent

    def get_active_count(self) -> int:
        """获取活跃 Agent 数量"""
        return len(self.active_agents)

    def list_active_agents(self) -> List[WebDevAgent]:
        """列出所有活跃 Agent"""
        return list(self.active_agents.values())


class GlobalAgentPool(BaseModel):
    """全局 Agent 池"""

    all_agents: Dict[str, WebDevAgent] = Field(
        default_factory=dict,
        description="所有 Agent，key 为 agent_id",
    )
    id_counter: int = Field(default=0, description="ID 计数器")

    def generate_agent_id(self) -> str:
        """生成唯一 Agent ID"""
        self.id_counter += 1
        suffix = os.urandom(2).hex()
        return f"WEB-{suffix}"

    def add_agent(self, agent: WebDevAgent) -> None:
        """添加 Agent 到全局池"""
        self.all_agents[agent.agent_id] = agent

    def get_agent(self, agent_id: str) -> Optional[WebDevAgent]:
        """获取 Agent"""
        return self.all_agents.get(agent_id)

    def update_agent(self, agent: WebDevAgent) -> None:
        """更新 Agent"""
        if agent.agent_id in self.all_agents:
            self.all_agents[agent.agent_id] = agent

    def get_active_agents(self) -> List[WebDevAgent]:
        """获取所有活跃的 Agent"""
        return [a for a in self.all_agents.values() if a.is_active()]

    def get_active_count(self) -> int:
        """获取活跃 Agent 数量"""
        return sum(1 for a in self.all_agents.values() if a.is_active())

    def get_agents_by_chat_key(self, chat_key: str) -> List[WebDevAgent]:
        """获取指定会话的所有 Agent"""
        return [a for a in self.all_agents.values() if a.chat_key == chat_key]

    def get_active_agents_by_chat_key(self, chat_key: str) -> List[WebDevAgent]:
        """获取指定会话的活跃 Agent"""
        return [a for a in self.all_agents.values() if a.chat_key == chat_key and a.is_active()]

    def get_stats(self) -> Dict[str, int]:
        """获取统计信息"""
        stats: Dict[str, int] = {
            "total": len(self.all_agents),
            "active": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        }
        for agent in self.all_agents.values():
            if agent.status == AgentStatus.COMPLETED:
                stats["completed"] += 1
            elif agent.status == AgentStatus.FAILED:
                stats["failed"] += 1
            elif agent.status == AgentStatus.CANCELLED:
                stats["cancelled"] += 1
            elif agent.is_active():
                stats["active"] += 1
        return stats


# ==================== 子 Agent 响应解析模型 ====================


class WebDevResponse(BaseModel):
    """子 Agent 响应解析结果"""

    progress_percent: int = Field(default=0, description="进度百分比")
    current_step: str = Field(default="", description="当前步骤")
    message_to_main: Optional[str] = Field(default=None, description="发给主 Agent 的消息")
    message_type: Optional[MessageType] = Field(default=None, description="消息类型")
    html_content: Optional[str] = Field(default=None, description="HTML 内容")
    page_title: Optional[str] = Field(default=None, description="页面标题")
    page_description: Optional[str] = Field(default=None, description="页面描述")
    raw_response: str = Field(default="", description="原始响应")
