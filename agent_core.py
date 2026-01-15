"""Multi-Agent Orchestration Core

This module manages the lifecycle, state, and communication of sub-agents within the plugin.
Migrated from nekro_agent.services.plugin.multi_agent to decouple from the core framework.
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

from nekro_agent.api.core import logger

if TYPE_CHECKING:
    from nekro_agent.api.plugin import NekroPlugin


class SubAgentStatus(str, Enum):
    """Sub-Agent Status"""

    PENDING = "pending"
    WORKING = "working"
    WAITING_INPUT = "waiting"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def is_active(self) -> bool:
        return self in (
            SubAgentStatus.PENDING,
            SubAgentStatus.WORKING,
            SubAgentStatus.WAITING_INPUT,
        )

    def is_terminal(self) -> bool:
        return self in (
            SubAgentStatus.COMPLETED,
            SubAgentStatus.FAILED,
            SubAgentStatus.CANCELLED,
        )


class AgentRole(str, Enum):
    """Agent Role Types
    
    All roles can create sub-agents if their task requires coordination.
    """
    
    ARCHITECT = "architect"  # System designer and coordinator
    ENGINEER = "engineer"    # Component implementer
    CREATOR = "creator"      # Content generator (can coordinate multi-chapter stories, etc.)
    
    def can_coordinate(self) -> bool:
        """All roles can coordinate sub-agents if needed"""
        return True


class PromptMode(str, Enum):
    """Prompt Mode for Agent
    
    Determines which prompt template to use based on agent's current role.
    """
    
    COORDINATOR = "coordinator"    # Has sub-agents, needs orchestration capabilities
    IMPLEMENTER = "implementer"    # Leaf node, focuses on implementation


class AgentMessage(BaseModel):
    """Message between Agents"""

    sender: str  # "main" | "sub" | agent_id
    content: str
    msg_type: str = "message"  # instruction, feedback, progress, result, question
    timestamp: int = Field(default_factory=lambda: int(time.time()))


class ChildSpec(BaseModel):
    """Task specification for a child agent"""

    role: str
    task: str
    output_format: str = ""
    context: str = ""
    constraints: List[str] = Field(default_factory=list)
    placeholder: str = ""
    difficulty: int = 3  # 1-5 stars
    allow_spawn_children: bool = True
    reuse: Optional[str] = None  # 复用已存在的 Agent ID（而非创建新 Agent）


class DeleteFileSpec(BaseModel):
    """文件删除规格"""
    path: str = Field(..., description="要删除的文件路径")
    confirmed: bool = Field(default=False, description="是否强制删除（即使文件正在被使用）")


class TransferFileSpec(BaseModel):
    """文件所有权转让规格"""
    path: str = Field(..., description="要转让的文件路径")
    to: str = Field(..., description="新所有者的 Agent ID")
    force: bool = Field(default=False, description="是否强制转让")


class AgentAction(BaseModel):
    """Structured decision output from an Agent"""

    # Child Management
    spawn_children: List[ChildSpec] = Field(default_factory=list)
    delegate_to: Dict[str, str] = Field(default_factory=dict)  # child_id -> message
    files: Dict[str, str] = Field(default_factory=dict)  # path -> content
    view_files: List[str] = Field(default_factory=list)  # paths to read
    dependencies: List[str] = Field(default_factory=list)  # e.g. ["tailwind", "framer-motion"]

    # File Ownership Management
    transfer_files: List[TransferFileSpec] = Field(default_factory=list)
    delete_files: List[DeleteFileSpec] = Field(default_factory=list)
    
    # Self action
    self_action: str = "none"
    self_output: Optional[str] = None

    # Status
    progress: int = 0
    step: str = ""
    message_to_parent: Optional[str] = None
    
    # Task Abortion (Last Resort)
    abort_task: bool = False
    abort_reason: Optional[str] = None


class SubAgent(BaseModel):
    """Base class for Sub-Agents"""

    model_config = {"extra": "allow"}

    agent_id: str
    chat_key: str
    task: str
    status: SubAgentStatus = SubAgentStatus.PENDING
    progress: int = Field(default=0, ge=0, le=100)
    current_step: str = ""
    messages: List[AgentMessage] = Field(default_factory=list)
    created_at: int = Field(default_factory=lambda: int(time.time()))
    updated_at: int = Field(default_factory=lambda: int(time.time()))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Hierarchy
    parent_id: Optional[str] = None
    children_ids: List[str] = Field(default_factory=list)
    level: int = 1
    role: str = ""

    # Spec
    spec: Optional[ChildSpec] = None

    # Output Management
    output_type: str = "html"
    output: Optional[Any] = None
    output_ready: bool = False
    template: Optional[str] = None
    child_outputs: Dict[str, str] = Field(default_factory=dict)

    def add_message(
        self, sender: str, content: str, msg_type: str = "message",
    ) -> AgentMessage:
        msg = AgentMessage(sender=sender, content=content, msg_type=msg_type)
        self.messages.append(msg)
        self.updated_at = int(time.time())
        return msg

    def update_progress(self, progress: int, step: str = "") -> None:
        self.progress = max(0, min(100, progress))
        if step:
            self.current_step = step
        self.updated_at = int(time.time())

    def is_active(self) -> bool:
        return self.status.is_active()

    def is_terminal(self) -> bool:
        return self.status.is_terminal()
    
    def is_leaf(self) -> bool:
        return len(self.children_ids) == 0

    def is_root(self) -> bool:
        return self.parent_id is None

    def get_aggregated_output(self) -> str:
        result = self.template or ""
        for role, fragment in self.child_outputs.items():
            result = result.replace("{{" + role + "}}", fragment)
        return result

    def set_child_output(self, role: str, output: str) -> None:
        self.child_outputs[role] = output
        self.updated_at = int(time.time())


T = TypeVar("T", bound=SubAgent)


class AgentPool(Generic[T]):
    """Manages a pool of agents"""

    def __init__(
        self,
        plugin: "NekroPlugin",
        agent_class: type[T],
        store_key: str,
        max_concurrent: int = 5,
        id_prefix: str = "A",
    ):
        self._plugin = plugin
        self._agent_class = agent_class
        self._store_key = f"_pool:{store_key}"
        self._max_concurrent = max_concurrent
        self._id_prefix = id_prefix
        self._id_counter: Dict[str, int] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, chat_key: str) -> asyncio.Lock:
        if chat_key not in self._locks:
            self._locks[chat_key] = asyncio.Lock()
        return self._locks[chat_key]

    def _generate_id(self, chat_key: str) -> str:
        if chat_key not in self._id_counter:
            self._id_counter[chat_key] = 0
        self._id_counter[chat_key] += 1
        return f"{self._id_prefix}{self._id_counter[chat_key]:04d}"

    async def load(self, chat_key: str) -> Dict[str, T]:
        data = await self._plugin.store.get(
            chat_key=chat_key, store_key=self._store_key,
        )
        if not data:
            return {}
        try:
            import json
            raw = json.loads(data)
            return {k: self._agent_class.model_validate(v) for k, v in raw.items()}
        except Exception as e:
            logger.error(f"Failed to load Agent Pool: {e}")
            return {}

    async def save(self, chat_key: str, agents: Dict[str, T]) -> None:
        import json
        data = json.dumps(
            {k: v.model_dump() for k, v in agents.items()}, ensure_ascii=False,
        )
        await self._plugin.store.set(
            chat_key=chat_key, store_key=self._store_key, value=data,
        )

    async def create(
        self,
        chat_key: str,
        task: str,
        wait_for_slot: bool = True,
        wait_timeout: int = 300,
        **kwargs: Any,
    ) -> T:
        start_time = time.time()
        while True:
            async with self._get_lock(chat_key):
                agents = await self.load(chat_key)
                active = sum(1 for a in agents.values() if a.is_active())
                if active < self._max_concurrent:
                    agent_id = self._generate_id(chat_key)
                    agent = self._agent_class(
                        agent_id=agent_id,
                        chat_key=chat_key,
                        task=task,
                        **kwargs,
                    )
                    agents[agent_id] = agent
                    await self.save(chat_key, agents)
                    logger.info(f"Created Sub-Agent: {agent_id} @ {chat_key}")
                    return agent

            if not wait_for_slot:
                raise ValueError(f"Max concurrent agents reached {self._max_concurrent}")

            elapsed = time.time() - start_time
            if elapsed >= wait_timeout:
                raise asyncio.TimeoutError(
                    f"Timeout waiting for agent slot ({wait_timeout}s)",
                )

            await asyncio.sleep(2)

    async def get(self, chat_key: str, agent_id: str) -> Optional[T]:
        agents = await self.load(chat_key)
        return agents.get(agent_id)

    async def update(self, agent: T) -> None:
        async with self._get_lock(agent.chat_key):
            agents = await self.load(agent.chat_key)
            agent.updated_at = int(time.time())
            agents[agent.agent_id] = agent
            await self.save(agent.chat_key, agents)

    async def get_active(self, chat_key: str) -> List[T]:
        agents = await self.load(chat_key)
        return [a for a in agents.values() if a.is_active()]

    async def get_all(self, chat_key: str) -> List[T]:
        agents = await self.load(chat_key)
        return list(agents.values())

    async def archive_completed(
        self, chat_key: str, max_age_seconds: int = 3600,
    ) -> int:
        async with self._get_lock(chat_key):
            agents = await self.load(chat_key)
            now = int(time.time())
            archived = 0
            for agent_id in list(agents.keys()):
                agent = agents[agent_id]
                if agent.is_terminal() and now - agent.updated_at > max_age_seconds:
                    del agents[agent_id]
                    archived += 1
            if archived:
                await self.save(chat_key, agents)
                logger.info(f"Archived {archived} completed agents @ {chat_key}")
            return archived

    async def spawn(
        self,
        parent: T,
        task: str,
        spec: Optional["ChildSpec"] = None,
        output_type: str = "json",
        **kwargs: Any,
    ) -> T:
        role = spec.role if spec else kwargs.pop("role", "")
        child = await self.create(
            chat_key=parent.chat_key,
            task=task,
            parent_id=parent.agent_id,
            level=parent.level + 1,
            role=role,
            spec=spec,
            output_type=output_type,
            **kwargs,
        )
        parent.children_ids.append(child.agent_id)
        await self.update(parent)
        logger.info(
            f"Spawned Sub-Agent: {child.agent_id} (role={role}) <- {parent.agent_id}",
        )
        return child

    async def reawaken(
        self,
        parent: T,
        agent_id: str,
        new_task: str,
        spec: Optional["ChildSpec"] = None,
    ) -> Optional[T]:
        """复活已完成的 Agent，注入新任务并重置状态

        Args:
            parent: 父 Agent（通常是 Architect）
            agent_id: 要复用的已有 Agent ID
            new_task: 新任务描述
            spec: 新的任务规格（可选）

        Returns:
            复活后的 Agent，如果找不到或状态不对则返回 None
        """
        async with self._get_lock(parent.chat_key):
            agents = await self.load(parent.chat_key)
            target = agents.get(agent_id)

            if not target:
                logger.warning(f"[Reawaken] Agent {agent_id} 未找到")
                return None

            if target.status == SubAgentStatus.WORKING:
                logger.warning(f"[Reawaken] Agent {agent_id} 正在工作中，无法复活")
                return None

            # 复活：重置状态，注入新任务
            target.status = SubAgentStatus.PENDING
            target.task = new_task
            target.progress = 0
            target.current_step = "reawakened"
            target.updated_at = int(time.time())

            # 添加系统消息告知历史
            target.add_message(
                "system",
                f"你已被重新激活以继续工作。新任务：{new_task}",
                "instruction",
            )

            if spec:
                target.spec = spec

            # 确保父子关系
            if agent_id not in parent.children_ids:
                parent.children_ids.append(agent_id)

            agents[agent_id] = target
            await self.save(parent.chat_key, agents)

            logger.info(f"[Reawaken] 复活 Agent {agent_id}，新任务: {new_task[:50]}...")
            return target


class MessageBus(Generic[T]):
    """Message Bus between Agents"""

    def __init__(self, pool: AgentPool[T]):
        self._pool = pool

    async def main_to_sub(
        self,
        chat_key: str,
        agent_id: str,
        content: str,
        msg_type: str = "instruction",
    ) -> bool:
        agent = await self._pool.get(chat_key, agent_id)
        if not agent or not agent.is_active():
            return False

        agent.add_message("main", content, msg_type)
        await self._pool.update(agent)
        return True

    async def sub_to_main(
        self,
        chat_key: str,
        agent_id: str,
        content: str,
        msg_type: str = "progress",
        trigger: bool = False,
    ) -> bool:
        agent = await self._pool.get(chat_key, agent_id)
        if not agent:
            return False

        agent.add_message("sub", content, msg_type)
        await self._pool.update(agent)

        if trigger:
            # We assume message_service is available globally via core import, 
            # OR we try to import it. In strict separation, this should be a callback.
            # For now, we keep the dynamic import if available.
            try:
                from nekro_agent.services.message_service import message_service
                await message_service.push_system_message(
                    chat_key=chat_key,
                    agent_messages=f"[{agent_id}] {content}",
                    trigger_agent=True,
                )
            except ImportError:
                 logger.warning("MessageService not found, skipping main trigger")
            
        return True


class StatusInjector(Generic[T]):
    """Injects sub-agent status into main agent context"""

    def __init__(
        self,
        pool: AgentPool[T],
        title: str = "Sub Agents",
        formatter: Optional[Callable[[T], str]] = None,
    ):
        self._pool = pool
        self._title = title
        self._formatter = formatter or self._default_format

    async def generate(self, chat_key: str) -> str:
        agents = await self._pool.get_active(chat_key)
        if not agents:
            return f"## {self._title}\nNo active agents."

        lines = [f"## {self._title} ({len(agents)})"]
        for agent in agents:
            lines.append(self._formatter(agent))
        return "\n".join(lines)

    def _default_format(self, agent: T) -> str:
        return (
            f"- **[{agent.agent_id}]** {agent.status.value} "
            f"({agent.progress}%) - {agent.current_step or agent.task[:30]}"
        )
