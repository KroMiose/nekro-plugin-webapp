"""文件操作工具

提供 write_file, read_file, apply_diff, list_files 等文件操作工具。
"""

import re
from typing import Any, Dict, List, Union

from ..core.context import ToolContext
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
async def write_file(ctx: ToolContext, path: str, content: str) -> str:
    """写入文件"""
    ctx.project.write_file(path, content)
    size = len(content)
    lines = content.count("\n") + 1
    return f"✅ 已写入 {path} ({lines} 行, {size} 字符)"


@agent_tool(
    name="read_file",
    description="读取文件内容。用于查看现有文件或检查导出。",
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
async def read_file(ctx: ToolContext, path: str) -> str:
    """读取文件"""
    content = ctx.project.read_file(path)
    if content is None:
        return f"❌ 文件不存在: {path}"

    lines = content.count("\n") + 1
    # 如果文件过长，截断显示
    if lines > 100:
        content_lines = content.split("\n")
        truncated = (
            "\n".join(content_lines[:50])
            + f"\n\n... 中间省略 {lines - 100} 行 ...\n\n"
            + "\n".join(content_lines[-50:])
        )
        return f"📄 {path} ({lines} 行，已截断)\n\n{truncated}"

    return f"📄 {path} ({lines} 行)\n\n{content}"


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
async def apply_diff(ctx: ToolContext, path: str, diff: str) -> str:
    """应用增量修改

    格式:
        <<<<<<< SEARCH
        原始内容
        =======
        新内容
        >>>>>>> REPLACE
    """
    content = ctx.project.read_file(path)
    if content is None:
        return f"❌ 文件不存在: {path}"

    # 解析 SEARCH/REPLACE 块
    pattern = r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE"
    matches = re.findall(pattern, diff, re.DOTALL)

    if not matches:
        return (
            "❌ 无效的 diff 格式，需要 <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE"
        )

    applied = 0
    errors = []

    for search, replace in matches:
        if search not in content:
            preview = search[:50] + "..." if len(search) > 50 else search
            errors.append(f"未找到: {preview}")
            continue

        content = content.replace(search, replace, 1)
        applied += 1

    if errors:
        return "❌ 部分修改失败:\n" + "\n".join(errors)

    ctx.project.write_file(path, content)
    return f"✅ 已应用 {applied} 处修改到 {path}"


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
async def delete_file(ctx: ToolContext, path: str) -> str:
    """删除文件"""
    if ctx.project.read_file(path) is None:
        return f"❌ 文件不存在: {path}"

    ctx.project.delete_file(path)
    return f"✅ 已删除 {path}"


@agent_tool(
    name="list_files",
    description="列出项目所有文件及其导出信息。",
    parameters={
        "type": "object",
        "properties": {},
    },
)
async def list_files(ctx: ToolContext) -> str:
    """列出所有文件"""
    files = ctx.project.list_files()

    if not files:
        return "📁 项目为空，尚无文件"

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

    return "\n".join(lines)


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
async def read_files(ctx: ToolContext, paths: Union[str, List[str]]) -> str:
    """读取多个文件内容

    Args:
        ctx: 工具上下文
        paths: 文件路径（逗号分隔字符串或列表）

    Returns:
        文件内容，每个文件用分隔线区分
    """
    # 处理参数格式
    if isinstance(paths, str):
        path_list = [p.strip() for p in paths.split(",") if p.strip()]
    else:
        path_list = paths

    if not path_list:
        return "❌ 未指定文件路径"

    # 限制最多读取 5 个文件
    if len(path_list) > 5:
        path_list = path_list[:5]

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
    return header + "\n\n".join(results)
