"""文件操作工具

提供 write_file, read_file, apply_diff, list_files 等文件操作工具。
所有工具统一返回 ToolResult 类型，tool_name 由框架自动注入。
"""

import re
from typing import List, Union

from ..core.context import ToolContext
from ..core.error_feedback import ErrorType, ToolResult
from . import agent_tool


@agent_tool(
    name="write_file",
    description="创建新文件或覆写现有文件。适用于新建文件或需要完整重写的场景。",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径，相对于 src 目录，如 'src/App.tsx'",
            },
            "content": {
                "type": "string",
                "description": "文件完整内容",
            },
        },
        "required": ["path", "content"],
    },
)
async def write_file(ctx: ToolContext, path: str, content: str) -> ToolResult:
    """写入文件（动作型工具，静默成功）"""
    ctx.project.write_file(path, content)
    size = len(content)
    lines = content.count("\n") + 1
    return ToolResult.ok(f"✅ 已写入 {path} ({lines} 行, {size} 字符)")


@agent_tool(
    name="read_file",
    description="读取单个文件内容。用于查看现有文件或检查导出。",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径，相对于 src 目录",
            },
        },
        "required": ["path"],
    },
)
async def read_file(ctx: ToolContext, path: str) -> ToolResult:
    """读取单个文件（查询型工具，反馈结果）"""
    content = ctx.project.read_file(path)
    if content is None:
        return ToolResult.ok(f"❌ 文件不存在: {path}", should_feedback=True)

    lines = content.count("\n") + 1
    # 如果文件过长，截断显示
    if lines > 100:
        content_lines = content.split("\n")
        truncated = (
            "\n".join(content_lines[:50])
            + f"\n\n... 中间省略 {lines - 100} 行 ...\n\n"
            + "\n".join(content_lines[-50:])
        )
        return ToolResult.ok(
            f"📄 {path} ({lines} 行，已截断)\n\n{truncated}",
            should_feedback=True,
        )

    return ToolResult.ok(f"📄 {path} ({lines} 行)\n\n{content}", should_feedback=True)


@agent_tool(
    name="apply_diff",
    description="使用 SEARCH/REPLACE 格式修改文件。比 write_file 更高效，适用于小范围修改。",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径",
            },
            "diff": {
                "type": "string",
                "description": "SEARCH/REPLACE 格式的修改内容",
            },
        },
        "required": ["path", "diff"],
    },
)
async def apply_diff(ctx: ToolContext, path: str, diff: str) -> ToolResult:
    """应用增量修改（动作型工具，静默成功）

    格式:
        <<<<<<< SEARCH
        原始内容
        =======
        新内容
        >>>>>>> REPLACE
    """
    content = ctx.project.read_file(path)
    if content is None:
        return ToolResult.error(
            message=f"文件不存在: {path}",
            error_type=ErrorType.FILE_NOT_FOUND,
            recoverable=True,
        )

    # 解析 SEARCH/REPLACE 块
    pattern = r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE"
    matches = re.findall(pattern, diff, re.DOTALL)

    if not matches:
        return ToolResult.error(
            message="无效的 diff 格式，需要 <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE",
            error_type=ErrorType.DIFF_NOT_FOUND,
            recoverable=True,
        )

    applied = 0
    errors: List[str] = []

    for search, replace in matches:
        # 检查匹配数量
        match_count = content.count(search)

        if match_count == 0:
            # 未找到匹配
            preview = search[:100] + "..." if len(search) > 100 else search
            errors.append(
                f"❌ 未找到匹配内容，请检查 SEARCH 部分是否与文件内容完全一致（包括空格和缩进）:\n"
                f"```\n{preview}\n```",
            )
            continue

        if match_count > 1:
            # 多处匹配，拒绝执行
            preview = search[:80] + "..." if len(search) > 80 else search
            errors.append(
                f"❌ 发现 {match_count} 处相同内容，无法确定替换哪一个。请扩展 SEARCH 块的上下文使其唯一:\n"
                f"```\n{preview}\n```",
            )
            continue

        # 唯一匹配，执行替换
        content = content.replace(search, replace, 1)
        applied += 1

    if errors:
        # 有错误时，返回详细反馈让 Agent 修正
        error_msg = f"DIFF 应用失败 ({len(errors)} 处错误, {applied} 处成功):\n\n" + "\n\n".join(errors)
        return ToolResult.ok(error_msg, should_feedback=True)

    ctx.project.write_file(path, content)
    return ToolResult.ok(f"✅ 已应用 {applied} 处修改到 {path}")


@agent_tool(
    name="delete_file",
    description="删除文件。",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径",
            },
        },
        "required": ["path"],
    },
)
async def delete_file(ctx: ToolContext, path: str) -> ToolResult:
    """删除文件（动作型工具，静默成功）"""
    if ctx.project.read_file(path) is None:
        return ToolResult.ok(f"❌ 文件不存在: {path}")

    ctx.project.delete_file(path)
    return ToolResult.ok(f"✅ 已删除 {path}")


@agent_tool(
    name="list_files",
    description="列出项目所有文件及其导出信息。",
    parameters={
        "type": "object",
        "properties": {},
    },
)
async def list_files(ctx: ToolContext) -> ToolResult:
    """列出所有文件（查询型工具，反馈结果）"""
    files = ctx.project.list_files()

    if not files:
        return ToolResult.ok("📁 项目为空，尚无文件", should_feedback=True)

    lines = ["📁 项目文件:"]
    for f in sorted(files):
        size = len(ctx.project.files.get(f, ""))

        # 提取导出信息
        exports_hint = ""
        if f.endswith((".ts", ".tsx")):
            exports = ctx.project.extract_exports(f)
            if exports:
                exports_str = ", ".join(exports[:5])
                if len(exports) > 5:
                    exports_str += f" (+{len(exports) - 5})"
                exports_hint = f" [exports: {exports_str}]"

        lines.append(f"  • {f} ({size} chars){exports_hint}")

    return ToolResult.ok("\n".join(lines), should_feedback=True)


@agent_tool(
    name="read_files",
    description="读取指定文件的内容。调用后必须停止输出，等待文件内容反馈。",
    parameters={
        "type": "object",
        "properties": {
            "paths": {
                "type": "string",
                "description": "要读取的文件路径，多个用逗号分隔，如 'src/App.tsx,src/utils.ts'",
            },
        },
        "required": ["paths"],
    },
)
async def read_files(ctx: ToolContext, paths: Union[str, List[str]]) -> ToolResult:
    """读取多个文件内容（查询型工具，反馈结果）

    Args:
        ctx: 工具上下文
        paths: 文件路径（逗号分隔字符串或列表）
    """
    # 处理参数格式
    if isinstance(paths, str):
        path_list = [p.strip() for p in paths.split(",") if p.strip()]
    else:
        path_list = paths

    if not path_list:
        return ToolResult.ok("❌ 未指定文件路径", should_feedback=True)

    # 限制单次最多读取 6 个文件
    MAX_FILES = 6
    remaining_paths: List[str] = []
    if len(path_list) > MAX_FILES:
        remaining_paths = path_list[MAX_FILES:]
        path_list = path_list[:MAX_FILES]

    results = []
    found_count = 0

    for path in path_list:
        content = ctx.project.read_file(path)
        if content:
            found_count += 1
            results.append(f"=== {path} ({len(content)} chars) ===\n{content}")
        else:
            results.append(f"=== {path} ===\n[文件不存在]")

    header = f"读取 {found_count}/{len(path_list)} 个文件:\n"
    body = "\n\n".join(results)

    # 如果有超出限制的文件，提示 Agent 再次调用
    if remaining_paths:
        remaining_str = ", ".join(remaining_paths)
        footer = (
            f"\n\n⚠️ 还有 {len(remaining_paths)} 个文件未读取: {remaining_str}\n"
            f'如需继续读取，请再次调用 @@READ paths="{remaining_str}"'
        )
        return ToolResult.ok(header + body + footer, should_feedback=True)

    return ToolResult.ok(header + body, should_feedback=True)

