"""流式响应解析器

支持边解析边启动子 Agent，实现并行执行。
"""

import contextlib
import re
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

import yaml
from pydantic import BaseModel

from nekro_agent.core import logger

from ..agent_core import AgentAction, ChildSpec, DeleteFileSpec, TransferFileSpec

if TYPE_CHECKING:
    from .task_tracer import TaskTracer


class ParseResult(BaseModel):
    """解析结果"""

    action: Optional[AgentAction] = None
    template: Optional[str] = None
    raw: str = ""
    dependencies: List[str] = []


class StreamParser:
    """流式响应解析器

    支持在 LLM 输出过程中检测 <spawn_children> 标记并立即启动子 Agent。

    Usage:
        ```python
        parser = StreamParser(on_children_ready=start_children)
        async for chunk in llm_stream:
            parser.feed(chunk)
        result = parser.get_result()
        ```
    """

    def __init__(
        self,
        on_children_ready: Optional[Callable[[List[ChildSpec]], None]] = None,
        tracer: Optional["TaskTracer"] = None,
        agent_id: str = "UNKNOWN",
    ):
        self.buffer = ""
        self.children_parsed = False
        self.on_children_ready = on_children_ready
        self.tracer = tracer
        self.agent_id = agent_id
        self._parsed_children: List[ChildSpec] = []
        self._spawn_parse_error: Optional[str] = None  # 解析失败时的错误信息

    @property
    def spawn_parse_error(self) -> Optional[str]:
        """获取解析失败时的错误信息"""
        return self._spawn_parse_error

    def feed(self, chunk: str) -> None:
        """接收新的文本块"""
        self.buffer += chunk

        # 检测子任务标记
        if not self.children_parsed and "</spawn_children>" in self.buffer:
            self._parse_spawn_children()

    def _parse_spawn_children(self) -> None:
        """解析 spawn_children 块（支持 YAML 或 XML 格式）"""
        match = re.search(
            r"<spawn_children>\s*(.*?)\s*</spawn_children>",
            self.buffer,
            re.DOTALL,
        )
        if not match:
            return

        content = match.group(1).strip()
        children_data = []

        # 尝试 1: 检测 XML 格式 (with <child> wrapper)
        if "<child>" in content:
            try:
                for child_match in re.finditer(
                    r"<child>(.*?)</child>",
                    content,
                    re.DOTALL,
                ):
                    child_content = child_match.group(1)
                    item = {}
                    for field in [
                        "role",
                        "task",
                        "output_format",
                        "context",
                        "difficulty",
                        "constraints",
                        "reuse",
                    ]:
                        if fm := re.search(
                            f"<{field}>(.*?)</{field}>",
                            child_content,
                            re.DOTALL,
                        ):
                            item[field] = fm.group(1).strip()
                    if item:
                        children_data.append(item)

                if children_data:
                    if self.tracer:
                        self.tracer.log_event(
                            "PARSE_FORMAT_XML",
                            self.agent_id,
                            "检测到 XML 格式指令 (with <child> wrapper)",
                        )
                    else:
                        logger.info(
                            "[WebDev] 🔍 检测到 XML 格式指令 (with <child> wrapper)",
                        )

            except Exception as e:
                if self.tracer:
                    self.tracer.log_event(
                        "PARSE_FAIL_XML",
                        self.agent_id,
                        f"解析 spawn_children (XML) 失败: {e}",
                    )
                else:
                    logger.warning(f"[WebDev] ⚠️ 解析 spawn_children (XML) 失败: {e}")

        # 尝试 2: 检测 Flat XML 格式 (直接 <role>...<task>... 无 <child> 包裹)
        # 这种格式下，每个 spawn_children 块只包含一个子任务
        if not children_data and "<role>" in content:
            try:
                item = {}
                for field in [
                    "role",
                    "task",
                    "output_format",
                    "context",
                    "difficulty",
                    "constraints",
                    "reuse",
                ]:
                    if fm := re.search(
                        f"<{field}>(.*?)</{field}>",
                        content,
                        re.DOTALL,
                    ):
                        item[field] = fm.group(1).strip()
                if item and item.get("role"):
                    children_data.append(item)
                    if self.tracer:
                        self.tracer.log_event(
                            "PARSE_FORMAT_FLAT_XML",
                            self.agent_id,
                            "检测到 Flat XML 格式指令 (无 <child> 包裹)",
                        )
                    else:
                        logger.info(
                            "[WebDev] 🔍 检测到 Flat XML 格式指令 (无 <child> 包裹)",
                        )

            except Exception as e:
                if self.tracer:
                    self.tracer.log_event(
                        "PARSE_FAIL_FLAT_XML",
                        self.agent_id,
                        f"解析 spawn_children (Flat XML) 失败: {e}",
                    )
                else:
                    logger.warning(
                        f"[WebDev] ⚠️ 解析 spawn_children (Flat XML) 失败: {e}",
                    )

        # 尝试 3: 检测 Attribute XML 格式 (e.g. <child role="..." />)
        if not children_data and "<child" in content:
            try:
                # 匹配自闭合 <child ... /> 或 <child ...>...</child> 的开始标签属性
                for child_match in re.finditer(
                    r"<child\s+([^>]+?)(?:/?>|>(.*?)</child>)",
                    content,
                    re.DOTALL,
                ):
                    attrs_str = child_match.group(1)
                    inner_content = (
                        child_match.group(2)
                        if (
                            child_match.lastindex is not None
                            and child_match.lastindex >= 2
                        )
                        else ""
                    )

                    item = {}

                    # 1. 解析属性
                    # 支持 key="value" 或 key='value'，处理换行和转义
                    for attr_match in re.finditer(
                        r'([a-zA-Z0-9_]+)\s*=\s*(["\'])(.*?)\2',
                        attrs_str,
                        re.DOTALL,
                    ):
                        key = attr_match.group(1)
                        val = attr_match.group(3)
                        # 处理 XML 转义字符
                        val = (
                            val.replace("&quot;", '"')
                            .replace("&apos;", "'")
                            .replace("&lt;", "<")
                            .replace("&gt;", ">")
                            .replace("&amp;", "&")
                        )
                        item[key] = val

                    # 2. 如果有 inner content (nested tags)，尝试从中提取补充字段覆盖属性
                    # 这允许混合模式：<child role="engineer"><task>...</task></child>
                    if inner_content:
                        for field in [
                            "role",
                            "task",
                            "output_format",
                            "context",
                            "difficulty",
                            "constraints",
                            "reuse",
                        ]:
                            if fm := re.search(
                                f"<{field}>(.*?)</{field}>",
                                inner_content,
                                re.DOTALL,
                            ):
                                item[field] = fm.group(1).strip()

                    if item and item.get("role"):
                        children_data.append(item)

                if children_data:
                    if self.tracer:
                        self.tracer.log_event(
                            "PARSE_FORMAT_ATTR_XML",
                            self.agent_id,
                            "检测到 Attribute XML 格式指令",
                        )
                    else:
                        logger.info("[WebDev] 🔍 检测到 Attribute XML 格式指令")

            except Exception as e:
                if self.tracer:
                    self.tracer.log_event(
                        "PARSE_FAIL_ATTR_XML",
                        self.agent_id,
                        f"解析 spawn_children (Attribute XML) 失败: {e}",
                    )
                else:
                    logger.warning(
                        f"[WebDev] ⚠️ 解析 spawn_children (Attribute XML) 失败: {e}",
                    )

        # 尝试 4: 如果不是 XML 格式，尝试 YAML
        if not children_data:
            try:
                data = yaml.safe_load(content)
                if isinstance(data, list):
                    children_data = data
                    if self.tracer:
                        self.tracer.log_event(
                            "PARSE_FORMAT_YAML_LIST",
                            self.agent_id,
                            "检测到 YAML 列表格式指令",
                        )
                    else:
                        logger.info("[WebDev] 🔍 检测到 YAML 列表格式指令")
                elif isinstance(data, dict):
                    children_data = [data]
                    if self.tracer:
                        self.tracer.log_event(
                            "PARSE_FORMAT_YAML_DICT",
                            self.agent_id,
                            "检测到 YAML 单项格式指令",
                        )
                    else:
                        logger.info("[WebDev] 🔍 检测到 YAML 单项格式指令")
            except Exception as e:
                if self.tracer:
                    self.tracer.log_event(
                        "PARSE_FAIL_YAML",
                        self.agent_id,
                        f"解析 spawn_children (YAML) 失败: {e}",
                    )
                else:
                    logger.warning(f"[WebDev] ⚠️ 解析 spawn_children (YAML) 失败: {e}")

        # 🚨 关键修复：如果检测到 spawn_children 标签但解析结果为空，必须报错！
        if not children_data:
            error_msg = (
                f"[WebDev] ❌ spawn_children 标签存在但解析失败！\n"
                f"支持的格式:\n"
                f"1. YAML 列表: - role: engineer\\n  task: ...\\n\n"
                f"2. XML with child: <child><role>...</role></child>\\n"
                f"3. Flat XML: <role>...</role><task>...</task>\\n"
                f"收到的内容 (前200字符):\n{content[:200]}"
            )
            if self.tracer:
                self.tracer.log_event("PARSE_FAIL_UNKNOWN", self.agent_id, error_msg)
            else:
                logger.error(error_msg)
            # 设置一个错误状态，让调用方知道解析失败
            self._spawn_parse_error = error_msg
            self.children_parsed = True  # 标记为已解析（虽然失败了），防止重复解析
            return

        for item in children_data:
            if not isinstance(item, dict):
                continue

            # 转换 difficulty
            diff_val = 3
            with contextlib.suppress(BaseException):
                diff_val = int(item.get("difficulty", 3))

            spec = ChildSpec(
                role=item.get("role", ""),
                task=item.get("task", ""),
                output_format=item.get("output_format", ""),
                context=item.get("context", ""),
                constraints=item.get("constraints", []),
                placeholder=item.get("placeholder", item.get("role", "")),
                difficulty=diff_val,
                reuse=item.get("reuse"),  # 复用已有 Agent
            )
            self._parsed_children.append(spec)

        self.children_parsed = True
        if self.tracer:
            self.tracer.log_event(
                "PARSE_SUCCESS",
                self.agent_id,
                f"解析到 {len(self._parsed_children)} 个子任务规格",
            )
        else:
            logger.info(f"[WebDev] 📋 解析到 {len(self._parsed_children)} 个子任务规格")

        if self.on_children_ready and self._parsed_children:
            self.on_children_ready(self._parsed_children)

    def _extract_template(self) -> Optional[str]:
        """提取模板内容"""
        match = re.search(
            r"<template>\s*(.*?)\s*</template>",
            self.buffer,
            re.DOTALL,
        )
        if match:
            return match.group(1).strip()
        return None

    def _extract_action(self) -> Optional[AgentAction]:
        """提取 AgentAction"""
        # 基础字段
        progress = 0
        step = ""
        self_action = "none"
        self_output = None
        message_to_parent = None
        delegate_to: Dict[str, str] = {}

        # 解析 status
        if m := re.search(r"<status>(.*?)</status>", self.buffer, re.DOTALL):
            content = m.group(1)
            if pm := re.search(r"progress[:\s]*(\d+)", content, re.I):
                progress = min(100, int(pm.group(1)))
            if sm := re.search(r"step[:\s]*(.+)", content, re.I):
                step = sm.group(1).strip()

        # 解析 template 存在意味着有 self_output
        template = self._extract_template()
        if template:
            self_action = "create" if not self._parsed_children else "modify"
            self_output = template

        # 解析 delegate
        for m in re.finditer(
            r"<delegate\s+to=[\"']([^\"']+)[\"']>(.*?)</delegate>",
            self.buffer,
            re.DOTALL,
        ):
            delegate_to[m.group(1)] = m.group(2).strip()

        # 解析 file
        files: Dict[str, str] = {}
        for m in re.finditer(
            r"<file\s+path=[\"']([^\"']+)[\"']>(.*?)</file>",
            self.buffer,
            re.DOTALL,
        ):
            files[m.group(1)] = m.group(2).strip()

        # 解析 view_file
        view_files: List[str] = []
        # Support both <view_file path="..." /> and <view_file path="..."></view_file>
        for m in re.finditer(
            r"<view_file\s+path=[\"']([^\"']+)[\"']\s*(?:/>|>(.*?)</view_file>)",
            self.buffer,
            re.DOTALL,
        ):
            view_files.append(m.group(1).strip())

        # 解析 message
        if m := re.search(r"<message>(.*?)</message>", self.buffer, re.DOTALL):
            message_to_parent = m.group(1).strip()

        # 解析 abort_task (Last Resort)
        # 格式: <abort_task reason="..." /> 或 <abort_task><reason>...</reason></abort_task>
        abort_task = False
        abort_reason = None
        if m := re.search(
            r"<abort_task\s+reason=[\"']([^\"']+)[\"']\s*/?>",
            self.buffer,
            re.DOTALL,
        ):
            abort_task = True
            abort_reason = m.group(1).strip()
        elif m := re.search(
            r"<abort_task>(.*?)</abort_task>",
            self.buffer,
            re.DOTALL,
        ):
            abort_task = True
            # 尝试提取 <reason> 标签
            if reason_m := re.search(r"<reason>(.*?)</reason>", m.group(1), re.DOTALL):
                abort_reason = reason_m.group(1).strip()
            else:
                abort_reason = m.group(1).strip()

        # 解析 transfer_ownership (所有权转让)
        # 格式: <transfer_ownership path="src/xxx.tsx" to="Web_0015" force="true"/>
        transfer_files: List[TransferFileSpec] = []
        for m in re.finditer(
            r"<transfer_ownership\s+path=[\"']([^\"']+)[\"']\s+to=[\"']([^\"']+)[\"'](?:\s+force=[\"']([^\"']*)[\"'])?\s*/>",
            self.buffer,
            re.DOTALL,
        ):
            path = m.group(1).strip()
            new_owner = m.group(2).strip()
            force = bool(m.group(3) and m.group(3).lower() == "true")
            transfer_files.append(
                TransferFileSpec(path=path, to=new_owner, force=force),
            )

        # 解析 delete_file (文件删除)
        # 格式: <delete_file path="src/xxx.tsx" confirmed="true"/>
        delete_files: List[DeleteFileSpec] = []
        for m in re.finditer(
            r"<delete_file\s+path=[\"']([^\"']+)[\"'](?:\s+confirmed=[\"']([^\"']*)[\"'])?\s*/>",
            self.buffer,
            re.DOTALL,
        ):
            path = m.group(1).strip()
            confirmed = bool(m.group(2) and m.group(2).lower() == "true")
            delete_files.append(DeleteFileSpec(path=path, confirmed=confirmed))

        return AgentAction(
            spawn_children=self._parsed_children,
            delegate_to=delegate_to,
            files=files,
            self_action=self_action,
            self_output=self_output,
            progress=progress,
            step=step,
            message_to_parent=message_to_parent,
            dependencies=self._extract_dependencies(),
            view_files=view_files,
            transfer_files=transfer_files,
            delete_files=delete_files,
            abort_task=abort_task,
            abort_reason=abort_reason,
        )

    def _extract_dependencies(self) -> List[str]:
        """提取依赖列表"""
        deps: List[str] = []
        if m := re.search(
            r"<dependencies>(.*?)</dependencies>",
            self.buffer,
            re.DOTALL,
        ):
            content = m.group(1).strip()
            # 支持每行一个或逗号分隔
            for part in re.split(r"[\s,]+", content):
                part = part.strip()
                if part and part not in deps:
                    deps.append(part)
        return deps

    def get_result(self) -> ParseResult:
        """获取最终解析结果"""
        return ParseResult(
            action=self._extract_action(),
            template=self._extract_template(),
            raw=self.buffer,
        )

    def get_children(self) -> List[ChildSpec]:
        """获取已解析的子 Agent 规格"""
        return self._parsed_children
