"""命令流解析器 (Command Stream Parser)

统一解析 LLM 纯文本输出，提取:
- 文件块: <<<FILE: path>>> ... <<<END_FILE>>>
- 工具命令: @@TOOL_NAME key="value" key2="value2"

Text-to-Tool Bridge 架构的核心组件。
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from nekro_agent.core.logger import logger


class CommandType(Enum):
    """命令类型"""

    FILE = "file"  # 文件写入
    TOOL_CALL = "tool_call"  # 工具调用


@dataclass
class ParsedCommand:
    """解析出的命令

    表示从文本流中解析出的一个可执行命令。
    """

    type: CommandType

    # FILE 类型
    file_path: Optional[str] = None
    file_content: Optional[str] = None
    file_complete: bool = False

    # TOOL_CALL 类型
    tool_name: Optional[str] = None
    tool_args: Dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        if self.type == CommandType.FILE:
            status = "完整" if self.file_complete else "未完成"
            return f"FILE({self.file_path}, {status})"
        return f"TOOL({self.tool_name}, {self.tool_args})"


@dataclass
class CommandStreamParser:
    """流式命令解析器

    职责：
    1. 累积流式文本
    2. 检测 FILE block 边界
    3. 检测 @@TOOL 命令
    4. 返回 ParsedCommand 供执行

    解析规则：
    - <<<FILE: path>>> ... <<<END_FILE>>> -> FILE 命令
    - @@TOOL_NAME arg1="val1" arg2="val2" -> TOOL_CALL 命令

    Example:
        parser = CommandStreamParser()

        async for chunk in llm_stream:
            for cmd in parser.feed(chunk):
                if cmd.type == CommandType.FILE:
                    vfs.write_file(cmd.file_path, cmd.file_content)
                elif cmd.type == CommandType.TOOL_CALL:
                    await execute_tool(cmd.tool_name, cmd.tool_args)

        # 流结束，检查未完成的文件
        incomplete = parser.flush()
        if incomplete:
            logger.warning(f"文件未完成: {incomplete.file_path}")
    """

    # 正则模式
    FILE_START_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"<<<FILE:\s*([^>]+)>>>"),
        repr=False,
    )
    FILE_END_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"<<<END_FILE>>>"),
        repr=False,
    )
    # @@TOOL_NAME key="value" key2="value2"
    # 工具命令必须在行首，参数用空格分隔，值用双引号包裹
    TOOL_CMD_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"^@@(\w+)(?:\s+(.+))?$", re.MULTILINE),
        repr=False,
    )
    ARG_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')'),
        repr=False,
    )

    buffer: str = ""
    """累积缓冲区"""

    current_file: Optional[str] = None
    """当前正在解析的文件路径"""

    current_content: str = ""
    """当前文件的累积内容"""

    def feed(self, chunk: str) -> List[ParsedCommand]:
        """处理增量文本

        Args:
            chunk: 来自 LLM 流的文本增量

        Returns:
            本次增量中完成的 ParsedCommand 列表
        """
        self.buffer += chunk
        commands: List[ParsedCommand] = []

        while True:
            consumed = False

            # 如果不在文件块内，检测工具命令和文件开始
            if self.current_file is None:
                # 1. 检测工具命令 (@@TOOL)
                # 只有当命令行完整（有换行符结尾）时才解析
                tool_match = self.TOOL_CMD_PATTERN.search(self.buffer)
                if tool_match:
                    # 检查命令是否在一个完整的行上
                    cmd_end = tool_match.end()
                    # 如果命令后面还有内容，或者 buffer 已经包含换行，说明命令完整
                    if cmd_end < len(self.buffer) or "\n" in self.buffer[tool_match.start() :]:
                        tool_name = tool_match.group(1).lower()
                        
                        # 别名映射
                        if tool_name == "read":
                            tool_name = "read_files"
                            
                        args_str = tool_match.group(2) or ""
                        # 处理正则的三组捕获: (key, val_double, val_single)
                        raw_args = self.ARG_PATTERN.findall(args_str)
                        args = {k: v1 or v2 for k, v1, v2 in raw_args}
                        logger.debug(f"[CommandParser] Raw Args Str: {args_str} -> Parsed: {args}")

                        commands.append(
                            ParsedCommand(
                                type=CommandType.TOOL_CALL,
                                tool_name=tool_name,
                                tool_args=args,
                            ),
                        )
                        logger.debug(f"[CommandParser] 解析到工具命令: {tool_name}({args})")

                        # 清理 buffer：移除命令行及其后的换行符
                        self.buffer = self.buffer[cmd_end:].lstrip("\n")
                        consumed = True
                        continue

                # 2. 检测文件开始
                file_match = self.FILE_START_PATTERN.search(self.buffer)
                if file_match:
                    self.current_file = file_match.group(1).strip()
                    self.current_content = ""
                    self.buffer = self.buffer[file_match.end() :]
                    logger.debug(f"[CommandParser] 文件开始: {self.current_file}")
                    consumed = True
                    continue

                # 没有匹配到任何模式，检查是否需要保留部分 buffer
                # 保留可能被截断的标记开头
                if "<<<" in self.buffer:
                    idx = self.buffer.rfind("<<<")
                    if idx > 0:
                        # 丢弃 <<< 之前的无用内容，继续循环尝试匹配
                        self.buffer = self.buffer[idx:]
                        continue
                elif "@@" in self.buffer:
                    idx = self.buffer.rfind("@@")
                    if idx > 0:
                        # 丢弃 @@ 之前的无用内容，继续循环尝试匹配
                        self.buffer = self.buffer[idx:]
                        continue
                # 没有可识别的标记前缀，或标记已在开头，退出等待更多输入
                break

            # 正在文件块内，寻找结束标记
            end_match = self.FILE_END_PATTERN.search(self.buffer)
            if end_match:
                # 找到结束标记
                self.current_content += self.buffer[: end_match.start()]
                commands.append(
                    ParsedCommand(
                        type=CommandType.FILE,
                        file_path=self.current_file,
                        file_content=self._clean_content(self.current_content),
                        file_complete=True,
                    ),
                )
                logger.debug(
                    f"[CommandParser] 文件完成: {self.current_file} "
                    f"({len(self.current_content)} 字符)",
                )

                # 重置状态，继续处理剩余内容
                self.buffer = self.buffer[end_match.end() :]
                self.current_file = None
                self.current_content = ""
                consumed = True
                continue
            # 未找到结束标记，继续累积
            # 保留可能被截断的 <<< 标记
            if "<<<" in self.buffer:
                idx = self.buffer.rfind("<<<")
                self.current_content += self.buffer[:idx]
                self.buffer = self.buffer[idx:]
            else:
                self.current_content += self.buffer
                self.buffer = ""
            break

            if not consumed:
                break

        return commands

    def flush(self) -> List[ParsedCommand]:
        """流结束时刷新缓冲区
        
        1. 尝试解析残留的工具命令
        2. 处理未完成的文件

        Returns:
            遗留的 ParsedCommand 列表
        """
        commands: List[ParsedCommand] = []

        # 1. 尝试解析残留的工具命令
        if self.buffer.strip():
            tool_match = self.TOOL_CMD_PATTERN.search(self.buffer)
            if tool_match:
                tool_name = tool_match.group(1).lower()
                # 别名映射
                if tool_name == "read":
                    tool_name = "read_files"
                
                args_str = tool_match.group(2) or ""
                # 处理正则的三组捕获: (key, val_double, val_single)
                raw_args = self.ARG_PATTERN.findall(args_str)
                args = {k: v1 or v2 for k, v1, v2 in raw_args}

                commands.append(
                    ParsedCommand(
                        type=CommandType.TOOL_CALL,
                        tool_name=tool_name,
                        tool_args=args,
                    ),
                )
                logger.debug(f"[CommandParser] Flush 解析到工具命令: {tool_name}({args})")
                self.buffer = ""

        # 2. 处理未完成的文件
        if self.current_file and self.current_content:
            commands.append(
                ParsedCommand(
                    type=CommandType.FILE,
                    file_path=self.current_file,
                    file_content=self._clean_content(self.current_content + self.buffer),
                    file_complete=False,
                ),
            )
            logger.warning(f"[CommandParser] 文件未完成: {self.current_file}")
            # 重置状态
            self.current_file = None
            self.current_content = ""
            self.buffer = ""
            
        return commands

    def reset(self) -> None:
        """重置解析器状态"""
        self.buffer = ""
        self.current_file = None
        self.current_content = ""

    def _clean_content(self, content: str) -> str:
        """清理内容

        移除开头和结尾的空行，但保留代码缩进。
        """
        lines = content.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    @property
    def is_parsing_file(self) -> bool:
        """是否正在解析文件"""
        return self.current_file is not None

    @property
    def current_parsing_file(self) -> Optional[str]:
        """当前正在解析的文件路径"""
        return self.current_file
