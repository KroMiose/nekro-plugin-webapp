"""子 Agent 工作循环

使用 AsyncTaskHandle 的 wait/notify 模式。
"""

import asyncio
import json
import re
import time
from contextlib import ExitStack
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from nekro_agent.api.core import ModelConfigGroup, logger
from nekro_agent.api.core import config as core_config
from nekro_agent.api.plugin import AsyncTaskHandle, TaskCtl, task
from nekro_agent.services.agent.creator import OpenAIChatMessage
from nekro_agent.services.agent.openai import gen_openai_chat_response

from ..agent_core import SubAgentStatus
from ..models import WebDevAgent, WebDevResponse
from ..plugin import config, plugin
from ..prompts import architect, common, content_creator, engineer, reviewer
from . import compiler_client, validator, vfs
from .deploy import deploy_html_to_worker
from .pool import get_agent, pool, send_to_main, send_to_sub, update_agent
from .prompt_logger import save_prompt_log_to_file
from .task_tracer import TaskTracer

TASK_TYPE = "webdev"


def enhance_compile_error(error_msg: str, chat_key: str) -> str:
    """增强编译错误信息，为常见错误添加帮助提示

    特别处理：
    - "No matching export" 错误：显示目标文件的实际导出列表
    - "File not found" 错误：提示可能的文件路径
    """
    enhanced = error_msg

    # 检测 "No matching export" 错误
    # 格式示例: No matching export in "vfs:src/data/story" for import "chapters"
    import_error_pattern = r'No matching export in "vfs:([^"]+)" for import "([^"]+)"'
    match = re.search(import_error_pattern, error_msg)

    if match:
        file_path = match.group(1)
        missing_export = match.group(2)

        # 尝试从 VFS 获取该文件的实际导出
        project_ctx = vfs.get_project_context(chat_key)

        # 添加 .ts 或 .tsx 扩展名尝试查找
        possible_paths = [file_path, f"{file_path}.ts", f"{file_path}.tsx"]
        for p in possible_paths:
            exports = project_ctx.extract_exports(p)
            if exports:
                # 格式化导出列表
                exports_str = ", ".join(exports[:10])
                if len(exports) > 10:
                    exports_str += f" (+{len(exports) - 10} more)"

                # 尝试建议正确的导入
                hint = f"\n\n💡 **Available exports in {p}**: {exports_str}"

                # 如果有类似的导出名，给出具体建议
                similar = [
                    e
                    for e in exports
                    if e.lower() == missing_export.lower()
                    or missing_export.lower() in e.lower()
                ]
                if similar:
                    hint += (
                        f"\n   Did you mean: `import {{ {similar[0]} }} from '...'` ?"
                    )

                enhanced += hint
                break

    # 检测 "File not found" 错误
    # 格式示例: File not found in VFS: src/components/StoryView
    file_not_found_pattern = r"File not found in VFS: ([^\s]+)"
    match = re.search(file_not_found_pattern, error_msg)

    if match:
        missing_file = match.group(1)
        project_ctx = vfs.get_project_context(chat_key)
        all_files = project_ctx.list_files()

        # 查找类似的文件名
        base_name = missing_file.split("/")[-1].lower()
        similar_files = [f for f in all_files if base_name in f.lower()]

        if similar_files:
            enhanced += (
                f"\n\n💡 **Similar files in VFS**: {', '.join(similar_files[:5])}"
            )

    return enhanced


# ==================== LLM 调用 ====================


async def call_llm(
    messages: List[OpenAIChatMessage],
    agent: WebDevAgent,
    tracer: TaskTracer,
) -> Tuple[Optional[str], Optional[str]]:
    """调用 LLM (流式)，支持降级和实时状态更新"""
    models = []
    if config.ADVANCED_MODEL_GROUP and agent.difficulty >= config.DIFFICULTY_THRESHOLD:
        models.append(config.ADVANCED_MODEL_GROUP)
    if config.WEBDEV_MODEL_GROUP and config.WEBDEV_MODEL_GROUP not in models:
        models.append(config.WEBDEV_MODEL_GROUP)

    with ExitStack() as stack:
        # 准备日志
        log_file = None
        if agent.agent_id:
            from datetime import datetime

            prompts_dir = plugin.get_plugin_path() / "prompts"
            prompts_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = prompts_dir / f"{timestamp}_{agent.agent_id}.log"
            tracer.log_event(
                "LLM_RESPONSE",
                agent.agent_id,
                f"收到 {len(messages)} 条消息",
            )
            try:
                log_file = stack.enter_context(log_path.open("w", encoding="utf-8"))
                # Log header
                log_file.write(f"=== LLM Call for {agent.agent_id} ===\n")
                log_file.write(f"Difficulty: {agent.difficulty}\n")
                log_file.write("=== Messages ===\n")
                for m in messages:
                    log_file.write(f"\n[{m.role}]\n{m.content}\n")
                log_file.write("\n=== Response ===\n")
                log_file.flush()
            except Exception as e:
                tracer.log_event(
                    "LOG_FILE_ERROR",
                    agent.agent_id,
                    f"Failed to create log file: {e}",
                )

        # 重置流状态
        agent.stream_start_time = time.time()
        agent.stream_chars = 0
        agent.stream_last_update = time.time()
        await update_agent(agent)

        for name in models:
            try:
                msg = f"调用 LLM: {name} (Streaming)"
                mg = core_config.get_model_group_info(name)
                # 记录 LLM 调用开始
                tracer.log_event(
                    "LLM_CALL_START",
                    agent.agent_id,
                    msg,
                    model=mg.CHAT_MODEL,
                    message_count=len(messages),
                )

                client = AsyncOpenAI(
                    api_key=mg.API_KEY,
                    base_url=mg.BASE_URL,
                )

                # Convert messages to dict
                openai_messages = []
                for m in messages:
                    openai_messages.append({"role": m.role, "content": m.content})

                # 保存提示词日志并注册到 tracer
                plugin_data_dir = str(plugin.get_plugin_data_dir())
                log_path = save_prompt_log_to_file(
                    agent.agent_id,
                    messages,
                    plugin_data_dir,
                )
                tracer.register_prompt_log(
                    agent_id=agent.agent_id,
                    round_num=agent.iteration_count + 1,
                    original_log_path=log_path,
                )

                stream = await client.chat.completions.create(
                    model=mg.CHAT_MODEL,
                    messages=openai_messages,
                    temperature=mg.TEMPERATURE,
                    top_p=mg.TOP_P,
                    stream=True,
                    extra_body=mg.EXTRA_BODY,
                    # Using standard params only as extra_body handles the rest?
                    # Need to be careful about non-standard params in mg
                )

                full_content = ""
                last_db_update = time.time()
                view_file_detected = False

                async for chunk in stream:
                    delta = chunk.choices[0].delta.content if chunk.choices else None
                    if delta:
                        full_content += delta
                        agent.stream_chars = len(full_content)

                        if log_file:
                            log_file.write(delta)

                        # 🔍 检测 view_file 标签 - 一旦完成就截断流
                        # 这样 Agent 可以立即看到文件内容，而不是盲写代码
                        if "</view_file>" in full_content and not view_file_detected:
                            view_file_detected = True
                            tracer.log_event(
                                "REVIEW_TRIGGER",
                                agent.agent_id,
                                "触发代码审查",
                            )
                            # 截断流，只保留到 view_file 结束的部分
                            # 忽略后续可能的 <file> 标签
                            break

                        # Update DB every 1s or every 100 chars (throttle)
                        # But don't update on every token!
                        now = time.time()
                        if now - last_db_update > 1.5:
                            agent.stream_last_update = now
                            await update_agent(agent)
                            last_db_update = now

                if full_content:
                    # Final update
                    agent.stream_chars = len(full_content)
                    await update_agent(agent)

                    msg = f"LLM 响应: {len(full_content)} 字符"
                    # 记录 LLM 调用结束
                    tracer.log_event(
                        "LLM_CALL_END",
                        agent.agent_id,
                        msg,
                        response_length=len(full_content),
                        model_used=name,
                    )

                    return full_content, None

            except Exception as e:
                tracer.log_event(
                    "LLM_CALL_FAIL",
                    agent.agent_id,
                    f"LLM 调用失败 ({name}): {e})",
                )
                if log_file:
                    log_file.write(f"\n\nERROR: {e}")

    return None, "所有模型调用失败"


# ==================== 响应解析 ====================


def parse_response(raw: str) -> WebDevResponse:
    """解析 LLM 响应"""
    result = WebDevResponse(raw_response=raw)

    # 进度
    if m := re.search(r"<status>(.*?)</status>", raw, re.DOTALL):
        content = m.group(1)
        if pm := re.search(r"progress[:\s]*(\d+)", content, re.I):
            result.progress_percent = min(100, int(pm.group(1)))
        if sm := re.search(r"step[:\s]*(.+)", content, re.I):
            result.current_step = sm.group(1).strip()

    # 消息
    if m := re.search(r"<message>(.*?)</message>", raw, re.DOTALL):
        result.message_to_main = m.group(1).strip()
        if result.message_to_main and (
            tm := re.search(r"type[:\s]*(\w+)", result.message_to_main, re.I)
        ):
            t = tm.group(1).lower()
            if "question" in t:
                result.message_type = "question"

    # HTML
    if m := re.search(r"<code>(.*?)</code>", raw, re.DOTALL):
        code = m.group(1).strip()
        if hm := re.search(
            r"```(?:html)?\s*\n?(<!DOCTYPE.*?</html>|<html.*?</html>)\s*```",
            code,
            re.DOTALL | re.I,
        ):
            result.html_content = hm.group(1).strip()
        elif code.startswith(("<!DOCTYPE", "<html")):
            result.html_content = code

    if not result.html_content and (
        hm := re.search(
            r"```html\s*\n?(<!DOCTYPE.*?</html>|<html.*?</html>)\s*```",
            raw,
            re.DOTALL | re.I,
        )
    ):
        result.html_content = hm.group(1).strip()

    # 标题和描述
    if result.html_content:
        # 从 <title> 提取标题
        if tm := re.search(r"<title>(.*?)</title>", result.html_content, re.I):
            result.page_title = result.page_title or tm.group(1).strip()

        # 从 HTML 注释提取 <!-- TITLE: xxx --> 和 <!-- DESC: xxx -->
        if tm := re.search(r"<!--\s*TITLE:\s*(.*?)\s*-->", result.html_content, re.I):
            result.page_title = tm.group(1).strip()
        if dm := re.search(r"<!--\s*DESC:\s*(.*?)\s*-->", result.html_content, re.I):
            result.page_description = dm.group(1).strip()

        # 从 meta description 提取
        if not result.page_description and (
            dm := re.search(
                r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
                result.html_content,
                re.I,
            )
        ):
            result.page_description = dm.group(1).strip()

        # 如果仍无描述，使用标题作为描述
        if not result.page_description and result.page_title:
            result.page_description = result.page_title

    # 最后尝试解析 <header> 块 (Agent 新标准)
    if m := re.search(r"<header>(.*?)</header>", raw, re.DOTALL):
        header_content = m.group(1)
        if tm := re.search(r"<title>(.*?)</title>", header_content, re.DOTALL):
            result.page_title = tm.group(1).strip()
        if dm := re.search(
            r"<description>(.*?)</description>",
            header_content,
            re.DOTALL,
        ):
            result.page_description = dm.group(1).strip()

    return result


# ==================== 状态更新辅助 ====================


async def set_status(agent: WebDevAgent, status: SubAgentStatus) -> None:
    """更新状态"""
    agent.status = status
    agent.updated_at = int(time.time())
    await update_agent(agent)


async def fail_agent(agent: WebDevAgent, error: str) -> None:
    """标记失败"""
    agent.status = SubAgentStatus.FAILED
    agent.error_message = error
    agent.updated_at = int(time.time())
    await update_agent(agent)


async def complete_agent(agent: WebDevAgent) -> None:
    """标记完成"""
    agent.status = SubAgentStatus.COMPLETED
    agent.progress = 100
    agent.complete_time = int(time.time())
    agent.updated_at = int(time.time())
    # 记录本次生成的总字符数
    agent.total_chars_generated = agent.stream_chars
    await update_agent(agent)


# ==================== HTML 生成 ====================

CORE_IMPORTS = {
    "react": "https://esm.sh/react@18.2.0",
    "react/jsx-runtime": "https://esm.sh/react@18.2.0/jsx-runtime?external=react",
    "react-dom": "https://esm.sh/react-dom@18.2.0?external=react",
    "react-dom/client": "https://esm.sh/react-dom@18.2.0/client?external=react",
    # Utilities often used without explicit declaration
    "clsx": "https://esm.sh/clsx@2.0.0?dev",
    "tailwind-merge": "https://esm.sh/tailwind-merge@2.0.0?dev",
}

OPTIONAL_IMPORTS = {
    # UI & Animation
    "framer-motion": "https://esm.sh/framer-motion@10.16.4?dev&external=react,react-dom",
    "lucide-react": "https://esm.sh/lucide-react@0.292.0?dev&external=react,react-dom",
    "lottie-react": "https://esm.sh/lottie-react@2.4.0?dev&external=react,react-dom",
    "canvas-confetti": "https://esm.sh/canvas-confetti@1.9.2?dev",
    "gsap": "https://esm.sh/gsap@3.12.5?dev",
    # State Management
    "zustand": "https://esm.sh/zustand@4.5.0?dev&external=react",
    "zustand/middleware": "https://esm.sh/zustand@4.5.0/middleware?dev&external=react",
    # Data & Math
    "date-fns": "https://esm.sh/date-fns@2.30.0?dev",
    "date-fns/locale": "https://esm.sh/date-fns@2.30.0/locale?dev",
    "lodash": "https://esm.sh/lodash@4.17.21?dev",
    "recharts": "https://esm.sh/recharts@2.12.0?dev&external=react,react-dom",
    "mathjs": "https://esm.sh/mathjs@12.3.0?dev",
    "papaparse": "https://esm.sh/papaparse@5.4.1?dev",
    "xlsx": "https://esm.sh/xlsx@0.18.5?dev",
    "axios": "https://esm.sh/axios@1.6.7?dev",
    # 3D & Graphics
    "three": "https://esm.sh/three@0.160.0?dev",
    "@react-three/fiber": "https://esm.sh/@react-three/fiber@8.15.14?dev&external=react,react-dom,three",
    "@react-three/drei": "https://esm.sh/@react-three/drei@9.96.1?dev&external=react,react-dom,three,@react-three/fiber",
    "@react-three/cannon": "https://esm.sh/@react-three/cannon@6.6.0?dev&external=react,react-dom,three,@react-three/fiber",
    "pixi.js": "https://esm.sh/pixi.js@7.3.2?dev",
    "@pixi/react": "https://esm.sh/@pixi/react@7.1.1?dev&external=react,react-dom,pixi.js",
    # Maps
    "leaflet": "https://esm.sh/leaflet@1.9.4?dev",
    "react-leaflet": "https://esm.sh/react-leaflet@4.2.1?dev&external=react,react-dom,leaflet",
    # Content & Media
    "react-markdown": "https://esm.sh/react-markdown@9.0.1?dev&external=react,react-dom",
    "howler": "https://esm.sh/howler@2.2.4?dev",
    "tone": "https://esm.sh/tone@14.7.77?dev",
    "mammoth": "https://esm.sh/mammoth@1.6.0?dev",
}


def generate_shell_html(
    title: str,
    body_js: str,
    dependencies: Optional[List[str]] = None,
) -> str:
    """生成最终的 Shell HTML，注按需注入脚本和样式"""

    if dependencies is None:
        dependencies = []
    scripts = []
    # 1. Tailwind (Heavy, optional)
    if "tailwind" in dependencies:
        scripts.append(
            '<script src="https://cdn.tailwindcss.com"></script>',
        )
    # 2. Leaflet CSS (Map)
    if "leaflet" in dependencies or "leaflet" in body_js:
        scripts.append(
            '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="" />',
        )
    # 3. KaTeX CSS (Math formulas)
    if "katex" in dependencies:
        scripts.append(
            '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css" integrity="sha384-n8MVd4RsNIU0tAv4ct0nTaAbDJwPJzDEaqSD1odI+WdtXRGWt2kTvGFasHpSy3SV" crossorigin="anonymous">',
        )

    scripts_html = "\n    ".join(scripts)

    # 动态构建 import map
    final_imports = CORE_IMPORTS.copy()

    # 扫描代码特征和依赖声明
    # 简单的启发式搜索: 检查 OPTIONAL_IMPORTS 的 key 是否出现在 body_js 中
    # 或者是否在 dependencies 列表中
    for pkg_name, url in OPTIONAL_IMPORTS.items():
        # 1. 显式声明
        if pkg_name in dependencies:
            final_imports[pkg_name] = url
            continue

        # 2. 代码引用检测 (简单字符串匹配)
        # 例如: import { Canvas } from "@react-three/fiber" -> 包含 "@react-three/fiber"
        # 或者 import * as THREE from "three"
        # 注意: body_js 是编译后的代码，esbuild 对于 external 模块会保留 import "pkg_name"
        if f'"{pkg_name}"' in body_js or f"'{pkg_name}'" in body_js:
            final_imports[pkg_name] = url

    # 自动解析隐式依赖：从 esm.sh URL 的 external= 参数提取依赖链
    # 例如: "external=react,react-dom,leaflet" -> 需要确保这些包也在 import map 中
    def extract_external_deps(esm_url: str) -> list[str]:
        """从 esm.sh URL 提取 external 参数中的依赖列表"""
        if "external=" not in esm_url:
            return []
        # 提取 external= 后的包列表 (可能被 & 截断)
        import re

        match = re.search(r"external=([^&]+)", esm_url)
        if match:
            return [dep.strip() for dep in match.group(1).split(",") if dep.strip()]
        return []

    # 遍历已添加的包，解析其 external 依赖并补充到 import map
    added_deps = True
    all_imports = {**CORE_IMPORTS, **OPTIONAL_IMPORTS}
    while added_deps:  # 循环直到没有新依赖被添加（处理传递依赖）
        added_deps = False
        for pkg_name, url in list(final_imports.items()):  # noqa: B007
            for dep in extract_external_deps(url):
                if dep not in final_imports and dep in all_imports:
                    final_imports[dep] = all_imports[dep]
                    added_deps = True

    import_map = {"imports": final_imports}

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title or "Nekro App"}</title>
    <style>
      /* Base styles to prevent white flash */
      html, body, #root {{ width: 100%; height: 100%; margin: 0; padding: 0; }}
    </style>
    {scripts_html}
    <script type="importmap">
    {json.dumps(import_map, indent=4)}
    </script>
    <script type="module">
{body_js}
    </script>
</head>
<body>
    <div id="root"></div>
</body>
</html>
"""


async def recompile_agent(agent_id: str, chat_key: str) -> str:
    """手动触发 Agent 产物重编译"""
    agent = await get_agent(agent_id, chat_key)
    if not agent:
        return f"❌ Agent {agent_id} 不存在"

    if agent.parent_id:
        return "❌ 只能对根 Agent (Root) 执行编译"

    project_ctx = vfs.get_project_context(chat_key)
    files = project_ctx.get_snapshot()

    if not files:
        return "❌ VFS 为空，无法编译"

    # Validation (Non-blocking check)
    validation_error = await compiler_client.check_project(
        files,
        agent.template_vars,
    )
    if validation_error and "error TS" in validation_error:
        # Just warn in the result string, proceed to build
        pass

    success, result, externals = await compiler_client.compile_project(
        files,
        env_vars=agent.template_vars,
    )

    if not success:
        return f"❌ 编译失败:\n{result[:500]}..."

    shell_html = generate_shell_html(
        title=agent.page_title or f"WebApp by {agent_id}",
        body_js=result,
        dependencies=agent.metadata.get("dependencies", []),
    )

    deployed_url = await deploy_html_to_worker(
        html_content=shell_html,
        title=agent.page_title or f"WebApp by {agent_id}",
        description=agent.page_description or "手动重新编译",
        template_vars={},
    )

    if deployed_url:
        agent.deployed_url = deployed_url
        await update_agent(agent)
        return f"✅ 编译并部署成功!\n🔗 URL: {deployed_url}"
    return "❌ 部署失败 (无 URL 返回)"


# ==================== 工作循环 ====================


@plugin.mount_async_task(TASK_TYPE)
async def agent_loop(
    handle: AsyncTaskHandle,
    agent_id: str,
    chat_key: str,
    tracer: TaskTracer,
) -> AsyncGenerator[TaskCtl, None]:
    """子 Agent 工作循环"""
    agent = await get_agent(agent_id, chat_key)
    if not agent or not agent.is_active():
        yield TaskCtl.fail("Agent 不存在或已结束")
        return

    # 使用 tracer 记录循环启动 (如果是根 Agent 首次启动)
    if not agent.parent_id:
        tracer.log_event(
            "TASK_START",
            agent_id,
            f"任务创建: {agent.task[:100]}",
            difficulty=agent.difficulty,
        )
    else:
        # 子 Agent 启动记录
        tracer.log_event(
            "AGENT_START",
            agent_id,
            f"Agent {agent_id} 循环启动",
            role=agent.role,
        )

    await set_status(agent, SubAgentStatus.WORKING)
    agent.start_time = int(time.time())
    await update_agent(agent)

    try:
        while True:
            if handle.is_cancelled:
                yield TaskCtl.cancel("已取消")
                return

            agent = await get_agent(agent_id, chat_key)
            if not agent:
                yield TaskCtl.fail("Agent 丢失")
                return

            # 🆕 检测是否为 reuse 模式（只在第一次循环时检测）
            if agent.iteration_count == 0 and len(agent.messages) > 1:
                project_ctx = vfs.get_project_context(chat_key)
                # 自动注入该 Agent 拥有的文件的最新内容
                owned_files = [
                    path
                    for path, owner in project_ctx.file_owners.items()
                    if owner == agent.agent_id
                ]

                if owned_files:
                    file_contexts = []
                    for path in owned_files[:3]:  # 最多注入 3 个文件
                        content = project_ctx.read_file(path)
                        if content:
                            file_contexts.append(
                                f"**{path}** (你之前创建的文件):\n```\n{content}\n```",
                            )

                    if file_contexts:
                        reuse_context = (
                            "📂 **你拥有的文件的最新内容**:\n\n"
                            + "\n\n".join(file_contexts)
                            + "\n\n请基于以上最新内容完成你的新任务。"
                        )
                        agent.add_message("system", reuse_context, "info")
                        await update_agent(agent)
                        tracer.log_event(
                            "REUSE_INJECTION",
                            agent.agent_id,
                            f"Reuse 模式：已为 {agent.agent_id} 注入 {len(owned_files)} 个文件",
                        )

            if agent.iteration_count >= config.MAX_ITERATIONS:
                await fail_agent(agent, f"达到最大迭代次数 ({config.MAX_ITERATIONS})")
                yield TaskCtl.fail("达到最大迭代")
                return

            yield TaskCtl.report_progress("调用 LLM", 20)

            # 预加载所有 Agent（解决异步环境中无法同步加载的问题）
            all_agents = await pool.load(chat_key)

            # 🔄 新逻辑：基于角色和子 Agent 数量判断 Prompt 模式
            # 任何 Agent 只要有子 Agent 或是根 Agent，就使用协调者模式（Architect Prompt）
            from ..agent_core import AgentRole, PromptMode

            # 判断是否需要协调者模式
            has_children = len(agent.children_ids) > 0
            is_root = agent.is_root()

            # 确定 Prompt 模式
            if has_children or is_root:
                # 协调者模式：需要管理子 Agent
                from ..prompts.architect import build_messages

                messages = build_messages(agent, all_agents)
                tracer.log_event(
                    "PROMPT_SELECT",
                    agent.agent_id,
                    f"使用 Architect 提示词 (协调者模式, role={agent.role}, children={len(agent.children_ids)}, is_root={is_root})",
                )

            else:
                # 实现者模式：根据角色选择对应的 Prompt
                try:
                    role = AgentRole(agent.role) if agent.role else AgentRole.ENGINEER
                except ValueError:
                    # 如果 role 不在枚举中，回退到 ENGINEER
                    tracer.log_event(
                        "ROLE_FALLBACK",
                        agent.agent_id,
                        f"未知角色 '{agent.role}'，回退到 ENGINEER",
                    )
                    role = AgentRole.ENGINEER

                if role == AgentRole.ENGINEER:
                    from ..prompts.engineer import build_messages

                    messages = build_messages(agent)
                    tracer.log_event(
                        "PROMPT_SELECT",
                        agent.agent_id,
                        "使用 Engineer 提示词 (实现者模式)",
                    )

                elif role == AgentRole.CREATOR:
                    from ..prompts.content_creator import build_messages

                    messages = build_messages(agent)
                    tracer.log_event(
                        "PROMPT_SELECT",
                        agent.agent_id,
                        "使用 Creator 提示词 (实现者模式)",
                    )

                else:
                    # ARCHITECT 或其他角色，使用 Architect Prompt
                    from ..prompts.architect import build_messages

                    messages = build_messages(agent, all_agents)
                    tracer.log_event(
                        "PROMPT_SELECT",
                        agent.agent_id,
                        f"使用 Architect 提示词 (role={role.value})",
                    )
            response_content, used_model = await call_llm(
                messages,
                agent,
                tracer=tracer,
            )

            if not response_content:
                err_msg = "LLM 返回空响应"
                await fail_agent(agent, err_msg)
                await send_to_main(
                    chat_key,
                    agent_id,
                    f"❌ {err_msg}",
                    trigger=True,
                    tracer=tracer,
                )
                yield TaskCtl.fail(err_msg)
                return

            # 使用 StreamParser 解析响应
            from .stream_parser import StreamParser

            parser = StreamParser(tracer=tracer, agent_id=agent.agent_id)
            parser.feed(response_content)
            parse_result = parser.get_result()
            action = parse_result.action

            # 🚨 检查 spawn_children 解析是否失败
            if parser.spawn_parse_error:
                error_feedback = (
                    f"❌ spawn_children 格式错误！你的指令无法被解析。\n\n"
                    f"{parser.spawn_parse_error}\n\n"
                    f"请使用正确的 YAML 格式重新发送 spawn_children 指令。例如:\n"
                    f"```yaml\n"
                    f"<spawn_children>\n"
                    f"- role: engineer\n"
                    f"  task: Create src/components/Example.tsx\n"
                    f"  difficulty: 3\n"
                    f"  context: |\n"
                    f"    File: src/components/Example.tsx\n"
                    f"</spawn_children>\n"
                    f"```"
                )
                agent.add_message("system", error_feedback)
                tracer.log_event(
                    "PARSE_ERROR_SPAWN",
                    agent.agent_id,
                    "spawn_children 解析失败，已反馈给 Agent",
                )
                agent.iteration_count += 1
                await update_agent(agent)
                continue  # 跳过本轮，让 Agent 重新发送

            tracer.log_event(
                "PARSE_RESULT",
                agent.agent_id,
                f"解析结果: 进度={action.progress if action else 0}%, 子任务={len(action.spawn_children) if action else 0}, Files={len(action.files) if action else 0}",
            )

            # 详细日志
            if action and action.spawn_children:
                for spec in action.spawn_children:
                    tracer.log_event(
                        "CHILD_PLAN",
                        agent.agent_id,
                        f"规划子任务: {spec.role} -> {spec.placeholder}",
                    )
            if parse_result.template:
                tracer.log_event(
                    "TEMPLATE_PARSED",
                    agent.agent_id,
                    f"模板长度: {len(parse_result.template)} 字符",
                )

            # 更新进度
            if action and (action.progress or action.step):
                agent.update_progress(action.progress, action.step)
            # 如果响应太长且没有代码块，截断后添加
            if response_content and len(response_content) > 500:
                agent.add_message(
                    "webdev",
                    response_content[:500] + "..."
                    if len(response_content) > 500
                    else response_content,
                )
            agent.iteration_count += 1
            tracer.log_event(
                "ITERATION_START",
                agent.agent_id,
                f"迭代 #{agent.iteration_count}",
            )
            await update_agent(agent)

            # ==================== 检查任务中止请求 (Last Resort) ====================
            if action and action.abort_task:
                abort_msg = (
                    f"🛑 Agent 主动中止任务\n"
                    f"原因: {action.abort_reason}\n\n"
                    f"这通常意味着任务存在根本性问题或系统内部错误，需要人工介入分析。"
                )
                tracer.log_event(
                    "TASK_ABORTED",
                    agent.agent_id,
                    abort_msg,
                    abort_reason=action.abort_reason,
                )

                # 保存 VFS 快照用于事后分析
                project_ctx = vfs.get_project_context(chat_key)
                tracer.save_vfs_snapshot(project_ctx)
                tracer.finalize(
                    final_status="ABORTED_BY_AGENT",
                    error_summary=action.abort_reason
                    or "Agent requested task abortion",
                )

                await fail_agent(agent, f"任务已中止: {action.abort_reason}")
                await send_to_main(
                    chat_key,
                    agent_id,
                    abort_msg,
                    trigger=True,
                    tracer=tracer,
                )
                yield TaskCtl.fail(f"任务已中止: {action.abort_reason}")
                return

            # ==================== view_file 优先级：如果同时请求查看和写入，先查看 ====================
            # 防止 Agent "盲写"：如果它在同一响应中既发 view_file 又写 file，说明它没看到内容就写了
            # 这种情况下，忽略本次写入，只返回文件内容，让 Agent 下一轮根据内容再写
            if action and action.view_files and action.files:
                project_ctx = vfs.get_project_context(chat_key)
                view_results = []
                for path in action.view_files:
                    content = project_ctx.read_file(path)
                    if content is None:
                        view_results.append(f"File not found: {path}")
                    else:
                        view_results.append(f"Content of {path}:\n```\n{content}\n```")

                # 🔄 新策略：中性化提示，不留“错误”痕迹
                context_injection = (
                    "🔍 File Contents:\n"
                    f"{chr(10).join(view_results)}\n\n"
                    "请基于以上文件内容继续你的工作。"
                )

                tracer.log_event(
                    "OUTPUT_SET",
                    agent.agent_id,
                    f"设置任务产出 ({len(context_injection)} chars)",
                )

                # 关键：不使用 "error" 类型，而是 "info" 类型
                agent.add_message("system", context_injection, "info")
                await update_agent(agent)

                # 清空本次的 files 字典，让 Agent 在下一轮重新决策
                action.files = {}
                continue  # 继续循环，等待 Agent 的下一轮响应

            # ==================== VFS 文件写入 ====================
            if action and action.files:
                project_ctx = vfs.get_project_context(chat_key)

                # 定义父子关系检查器（同步版本，使用默认参数捕获 all_agents）
                def check_parent_child_relation(
                    writer_id: str,
                    owner_id: str,
                    agents=all_agents,
                ) -> bool:
                    """检查 writer 是否为 owner 的父 Agent"""
                    owner_agent = agents.get(owner_id)
                    return (
                        owner_agent is not None and owner_agent.parent_id == writer_id
                    )

                # 定义状态检查器
                def check_owner_status(owner_id: str, agents=all_agents) -> str:
                    """检查 Agent 的状态"""
                    owner_agent_sync = agents.get(owner_id)
                    if owner_agent_sync:
                        return owner_agent_sync.status.value
                    return "unknown"

                for path, content in action.files.items():
                    # Validation Hook
                    valid = True
                    err_msg = ""

                    if path.endswith(".json"):
                        valid, err_msg = validator.validator.validate_json(content)
                    elif path.endswith((".ts", ".tsx", ".js", ".jsx")):
                        valid, err_msg = validator.validator.validate_typescript(
                            content,
                        )

                    if not valid:
                        tracer.log_event(
                            "FILE_ERROR",
                            agent.agent_id,
                            f"文件验证失败: {path} - {err_msg}",
                        )
                        # 给 Agent 发送反馈，要求修正
                        # 但为了简化控制流，暂时只记录 log，并追加到 agent message
                        agent.add_message(
                            "system",
                            f"⚠️ File '{path}' validation failed: {err_msg}",
                            "feedback",
                        )
                        # 暂时允许写入，或者拒绝？
                        # 拒绝写入更安全：
                        continue

                    tracer.log_event(
                        "FILE_WRITE",
                        agent.agent_id,
                        f"写入文件: {path} ({len(content)} chars)",
                    )
                    write_result = project_ctx.write_file(
                        path,
                        content,
                        agent_id=agent.agent_id,
                        parent_id_checker=check_parent_child_relation,
                        owner_status_checker=check_owner_status,
                    )

                    # 处理所有权冲突
                    if not write_result.success:
                        agent.add_message(
                            "system",
                            f"🚫 文件写入失败: {write_result.error}",
                            "error",
                        )
                        await update_agent(agent)
                        # 跳过此文件，继续处理其他文件
                        continue

                # 重置 Review 状态，因为代码变了，必须重新审查
                # ⚠️ 注意：不重置 review_rounds，让其正常累计
                # 这样可以确保在多次失败后触发强制交付，避免无限循环
                if "review_status" in agent.metadata:
                    tracer.log_event(
                        "REVIEW_STATUS_CLEAR",
                        agent.agent_id,
                        f"检测到代码修改（写入 {len(action.files)} 个文件），清除审查状态，当前轮次: {agent.review_rounds}",
                    )
                    # 只清除审查状态，让下一轮重新审查
                    # 不重置 review_rounds，让其累计
                    del agent.metadata["review_status"]
                    await update_agent(agent)


            # ==================== VFS 文件读取 (View Files) ====================
            if action and action.view_files:
                project_ctx = vfs.get_project_context(chat_key)
                view_results = []
                for path in action.view_files:
                    content = project_ctx.read_file(path)
                    if content is None:
                        view_results.append(f"File not found: {path}")
                    else:
                        view_results.append(f"Content of {path}:\n```\n{content}\n```")

                if view_results:
                    view_content = "\n\n".join(view_results)
                    tracer.log_event(
                        "VIEW_FILES_RETURN",
                        agent.agent_id,
                        f"读取 {len(view_results)} 个文件返回给 Agent",
                    )
                    _content_str = f"🔍 File Contents:\n{view_content}"
                    agent.add_message(
                        sender="system",
                        content=_content_str,
                        msg_type="system",
                    )
                    await update_agent(agent)
                    # 如果只是查看文件，建议跳过本次编译（节省资源），直接进入下一次思考
                    # 除非同时也修改了文件
                    if not action.files and not action.spawn_children:
                        continue

            # ==================== VFS 所有权转让 (Transfer Ownership) ====================
            if action and action.transfer_files:
                project_ctx = vfs.get_project_context(chat_key)
                for transfer_spec in action.transfer_files:
                    tracer.log_event(
                        "VFS_TRANSFER",
                        agent.agent_id,
                        f"转让所有权: {transfer_spec.path} -> {transfer_spec.to}",
                    )
                    project_ctx.transfer_ownership(
                        transfer_spec.path,
                        transfer_spec.to,
                        force=transfer_spec.force,
                    )
                    agent.add_message(
                        "system",
                        f"✅ 文件 {transfer_spec.path} 的所有权已转让给 {transfer_spec.to}",
                        "info",
                    )
                await update_agent(agent)

            # ==================== VFS 文件删除 (Delete Files) ====================
            if action and action.delete_files:
                project_ctx = vfs.get_project_context(chat_key)
                # 获取当前所有 WORKING 状态的 Agent ID
                all_agents = await pool.load(chat_key)
                working_agent_ids = [
                    a.agent_id
                    for a in all_agents.values()
                    if a.status == SubAgentStatus.WORKING
                ]
                for delete_spec in action.delete_files:
                    tracer.log_event(
                        "DELETE_FILE",
                        agent.agent_id,
                        f"删除文件: {delete_spec.path} (confirmed={delete_spec.confirmed})",
                    )
                    result = project_ctx.delete_file(
                        delete_spec.path,
                        confirmed=delete_spec.confirmed,
                        working_agents=working_agent_ids,
                    )
                    if result.success:
                        agent.add_message(
                            "system",
                            f"✅ 文件 {delete_spec.path} 已删除",
                            "info",
                        )
                    else:
                        agent.add_message(
                            "system",
                            f"🚫 删除失败: {result.error}",
                            "error",
                        )
                await update_agent(agent)

            # 如果有文件写入，认为已有产出
            if action and action.files:
                agent.output_ready = True

            # Check for dependencies (独立于 view_files 和 files)
            if action and action.dependencies:
                tracer.log_event(
                    "DEPENDENCY",
                    agent.agent_id,
                    f"声明依赖: {action.dependencies}",
                )
                current_deps = agent.metadata.get("dependencies", [])
                for d in action.dependencies:
                    if d not in current_deps:
                        current_deps.append(d)
                agent.metadata["dependencies"] = current_deps

            await update_agent(agent)

            # ==================== 子 Agent 管理 ====================

            # 2. 创建新子 Agent
            spawned_children = []
            if action and action.spawn_children:
                # 在纯 VFS 模式下，不再校验 placeholder 与 template 的对应关系
                # 因为子 Agent 是通过写文件 (File Import) 协作的，而不是字符串替换

                for spec in action.spawn_children:
                    # 检查是否复用已有 Agent
                    if spec.reuse:
                        tracer.log_event(
                            "CHILD_REUSE_ATTEMPT",
                            agent.agent_id,
                            f"尝试复用 Agent: {spec.reuse}",
                        )
                        child = await pool.reawaken(
                            agent,
                            spec.reuse,
                            spec.task,
                            spec=spec,
                        )
                        if child:
                            spawned_children.append(child)
                            await start_agent_task(child.agent_id, chat_key, tracer)

                            msg = f"复用 Agent {child.agent_id}: {spec.task[:50]}..."
                            tracer.log_event(
                                "CHILD_REUSED",
                                agent.agent_id,
                                msg,
                                child_id=child.agent_id,
                                task=spec.task[:200],
                                role=spec.reuse,
                            )
                        else:
                            # 复用失败，回退到创建新 Agent
                            tracer.log_event(
                                "CHILD_REUSE_FAIL",
                                agent.agent_id,
                                f"复用 {spec.reuse} 失败，创建新 Agent",
                            )
                            child = await pool.spawn(
                                agent,
                                spec.task,
                                spec=spec,
                                difficulty=spec.difficulty,
                            )
                            spawned_children.append(child)
                            await start_agent_task(child.agent_id, chat_key, tracer)
                            tracer.log_event(
                                "CHILD_SPAWNED_FALLBACK",
                                agent.agent_id,
                                f"子 Agent {child.agent_id} 已启动 (Reuse Failed Fallback)",
                            )
                    else:
                        tracer.log_event(
                            "CHILD_CREATE",
                            agent.agent_id,
                            f"创建子 Agent: role={spec.role}, task={spec.task[:50]}...",
                        )
                        child = await pool.spawn(
                            agent,
                            spec.task,
                            spec=spec,
                            difficulty=spec.difficulty,
                        )
                        spawned_children.append(child)
                        await start_agent_task(child.agent_id, chat_key, tracer)

                        msg = f"子 Agent {child.agent_id} ({spec.role}) 已启动"
                        tracer.log_event(
                            "CHILD_SPAWNED",
                            agent.agent_id,
                            msg,
                            child_id=child.agent_id,
                            task=spec.task[:200],
                            role=spec.role,
                            difficulty=spec.difficulty,
                        )
                await update_agent(agent)

            # 3. 转发请求给现有子 Agent
            if action and action.delegate_to:
                for child_id, message in action.delegate_to.items():
                    msg = f"📨 向子 Agent {child_id} 发送指令: {message[:50]}..."
                    tracer.log_event(
                        "DELEGATION",
                        agent.agent_id,
                        msg,
                        child_id=child_id,
                        instruction=message[:200],
                    )

                    await send_to_sub(
                        chat_key,
                        child_id,
                        message,
                        "instruction",
                        tracer=tracer,
                    )
                    await wake_up_agent(child_id, chat_key, message)

            # 4. 等待所有活跃子 Agent 完成（轮询方式）
            if spawned_children or (action and action.delegate_to):
                active_child_ids = [c.agent_id for c in spawned_children]
                if action and action.delegate_to:
                    active_child_ids.extend(action.delegate_to.keys())

                logger.info(
                    f"⏳ 等待 {len(active_child_ids)} 个子 Agent 完成任务...",
                )

                # 轮询等待子 Agent 完成
                start_wait = time.time()
                poll_interval = 3  # 每 3 秒检查一次
                timeout = config.AGENT_TIMEOUT_MINUTES * 60

                while active_child_ids:
                    if handle.is_cancelled:
                        tracer.log_event("WAITING_CANCEL", agent.agent_id, "等待被取消")
                        break

                    # 检查超时
                    if time.time() - start_wait > timeout:
                        tracer.log_event(
                            "CHILD_TIMEOUT",
                            agent.agent_id,
                            "等待子 Agent 超时",
                        )
                        break

                    # 检查每个子 Agent 的状态
                    for child_id in list(active_child_ids):
                        child = await get_agent(child_id, chat_key)
                        if child and child.is_terminal():
                            # 子 Agent 已完成
                            if child.output:
                                # 优先使用 spec.placeholder，否则使用 role 或 agent_id
                                spec_placeholder = (
                                    child.spec.placeholder
                                    if child.spec and child.spec.placeholder
                                    else None
                                )

                                msg = f"✅ 子 Agent {child_id} 任务完成"
                                tracer.log_event(
                                    "CHILD_COMPLETE",
                                    agent.agent_id,
                                    msg,
                                    child_id=child_id,
                                    output_key=spec_placeholder
                                    or child.role
                                    or child_id,
                                )

                                # 在纯 VFS 模式下，我们不需要收集字符串产物进行替换
                                # 但为了保持兼容性，我们还是存一下，虽然不会被用到
                                key = spec_placeholder or child.role or child_id
                                agent.set_child_output(key, "VFS_UPDATED")
                            else:
                                logger.warning(
                                    f"⚠️ 子 Agent {child_id} 已完成但无产物",
                                )
                            active_child_ids.remove(child_id)

                    if active_child_ids:
                        # 还有未完成的子 Agent，等待后继续轮询
                        await asyncio.sleep(poll_interval)

                await update_agent(agent)
                logger.info(
                    f"✅ 所有子 Agent 处理完成，收集到 {len(agent.child_outputs)} 个产物",
                )

            # ==================== 本层产物处理 ====================

            deployed_url: Optional[str] = None

            # 使用 template 或 code 块中的 HTML
            template_content = parse_result.template
            parsed = parse_response(response_content)  # 兼容旧格式
            logger.debug(
                f"Agent {agent.agent_id} 响应: {response_content[:100]}...",
            )
            html_content = template_content or parsed.html_content

            # ==================== 1. 模板聚合 (已移除) ====================
            # 我们转向纯 VFS 架构，不再进行字符串替换。
            # VFS 中的文件即为最终源码。
            if html_content and agent.child_outputs:
                tracer.log_event(
                    "LEGACY_IGNORE",
                    agent.agent_id,
                    "忽略旧版子产物聚合 (Using Pure VFS)",
                )

            # 更新 Agent 数据
            if html_content:
                agent.current_html = html_content
            agent.template = template_content
            agent.page_title = parsed.page_title
            agent.page_description = parsed.page_description
            await update_agent(agent)

            # ==================== 2. 提交(Child) 或 部署(Root) ====================

            if agent.parent_id:
                # ---------- 子 Agent: 提交产物给父 Agent ----------
                output_content = None

                # 优先级 1: HTML 内容 (模板渲染结果)
                if html_content:
                    output_content = html_content

                # 优先级 2: 显式输出 (Self Output / Message)
                elif action and action.self_output:
                    output_content = action.self_output
                elif action and action.message_to_parent:
                    output_content = action.message_to_parent

                # 优先级 3: 兜底 (使用 Raw Response)
                elif agent.output_ready:
                    # 如果写了文件但没有显式输出，可能意味着工作已完成
                    # 使用 response_content 作为上下文
                    output_content = response_content

                if output_content:
                    agent.output = output_content
                    agent.output_ready = True
                    await update_agent(agent)

                    parent_handle = task.get_handle(TASK_TYPE, agent.parent_id)
                    if parent_handle:
                        parent_handle.notify(f"child:{agent_id}", output_content)
                        logger.info(
                            f"📤 子 Agent {agent_id} 产物已提交给父 Agent {agent.parent_id}",
                        )

                    # 子 Agent 完成
                    await complete_agent(agent)
                    yield TaskCtl.success("子产物已提交", data=output_content[:100])
                    return

            else:
                # ---------- 顶层 Agent: 编译与部署 ----------
                deploy_success = False

                # 1. 检查 VFS (React 模式)
                project_ctx = vfs.get_project_context(chat_key)
                files = project_ctx.get_snapshot()

                if files:
                    tracer.log_event(
                        "BUILD_START",
                        agent.agent_id,
                        f"开始编译项目 ({len(files)} files)",
                    )

                    # 1. Strict Validation via TSC
                    validation_error = await compiler_client.check_project(
                        files,
                        agent.template_vars,
                    )
                    if validation_error:
                        # Ignore generic module errors if library not installed locally
                        if (
                            "Cannot find module" in validation_error
                            and "lucide-react" not in validation_error
                        ):
                            pass
                        # Fail on defined semantic errors
                        elif "error TS" in validation_error:
                            logger.warning(
                                f"❌ 编译前检查失败:\n{validation_error}",
                            )
                            # Critical errors
                            if (
                                "is not defined" in validation_error
                                or "not assignable" in validation_error
                            ):
                                raise RuntimeError(  # noqa: TRY301
                                    f"Type Check Failed:\n{validation_error}",
                                )

                    # 2. Build
                    # 将 template_vars 作为环境变量注入到 process.env
                    # 4. 编译项目
                    # 4. 编译项目
                    success, result, externals = await compiler_client.compile_project(
                        files,
                        env_vars=agent.template_vars,
                        tracer=tracer,
                        agent_id=agent.agent_id,
                    )

                    # 4.1 验证外部依赖 (Build-time Import Map Check)
                    if success:
                        allowed_imports = set(CORE_IMPORTS.keys()) | set(
                            OPTIONAL_IMPORTS.keys(),
                        )
                        # 兼容 React 生态
                        allowed_imports.add("react")
                        allowed_imports.add("react-dom")
                        allowed_imports.add("react-dom/client")
                        allowed_imports.add("react/jsx-runtime")

                        unsupported_deps = []
                        for ext in externals:
                            if ext not in allowed_imports:
                                # 尝试检查是否是子路径 (且根包被允许)
                                # 目前只允许明确映射的包 (Import Map 限制)
                                unsupported_deps.append(ext)

                        if unsupported_deps:
                            success = False
                            error_details = "\n".join(
                                [f"- {dep}" for dep in unsupported_deps],
                            )
                            result = (
                                f"❌ Build Failed: The following dependencies are NOT supported in this environment:\n{error_details}\n\n"
                                "Please check if you need to:\n"
                                "1. Use a supported library from the `PROMPT` list.\n"
                                "2. Use a direct CDN import in a `<script>` tag instead of `import`.\n"
                                "3. Ask the administrator to add support for this library."
                            )

                            # 记录缺失的依赖到 KV 存储
                            try:
                                from ..plugin import plugin

                                store_key = "global_missing_dependencies"
                                existing_bytes = await plugin.store.get(
                                    store_key=store_key,
                                )

                                # Migration: List[str] -> Dict[str, int]
                                existing_data = {}
                                if existing_bytes:
                                    loaded = json.loads(existing_bytes)
                                    if isinstance(loaded, list):
                                        existing_data = dict.fromkeys(loaded, 1)
                                    elif isinstance(loaded, dict):
                                        existing_data = loaded

                                updated = False
                                for dep in unsupported_deps:
                                    if dep not in existing_data:
                                        existing_data[dep] = 0
                                    existing_data[dep] += 1
                                    updated = True

                                if updated:
                                    await plugin.store.set(
                                        store_key=store_key,
                                        value=json.dumps(existing_data),
                                    )
                                    logger.info(
                                        f"Updated missing dependencies counts: {existing_data.keys()}",
                                    )
                            except Exception as e:
                                logger.warning(
                                    f"Failed to record missing dependencies: {e}",
                                )

                    if success:
                        agent.consecutive_failures = 0  # 重置失败计数
                        tracer.log_event(
                            "BUILD_SUCCESS",
                            agent.agent_id,
                            f"编译成功 ({len(result)} chars)",
                        )

                        # 部署前检测: 扫描编译产物中是否包含浏览器无法解析的裸模块 import
                        # 例如: import "leaflet/dist/leaflet.css" 或 import "some-package"
                        # 浏览器只能解析相对路径 (./ ../) 或完整 URL，裸模块需要 import map 支持
                        bare_import_pattern = r'import\s*["\']([^./][^"\']*)["\']'
                        bare_imports = re.findall(bare_import_pattern, result)

                        # 过滤掉 import map 中已配置的包
                        known_packages = set(CORE_IMPORTS.keys()) | set(
                            OPTIONAL_IMPORTS.keys(),
                        )
                        invalid_imports = [
                            imp
                            for imp in bare_imports
                            if imp not in known_packages and not imp.startswith("http")
                        ]

                        if invalid_imports:
                            error_msg = (
                                f"❌ 编译产物包含浏览器无法解析的裸模块导入: {invalid_imports}\n"
                                "这些导入在浏览器中会报错。请检查:\n"
                                "1. 对于 CSS 文件，不要手动 import，使用 <dependencies> 声明即可\n"
                                "2. 对于 JS 库，确保在预装库列表中或使用 CDN URL"
                            )
                            tracer.log_event(
                                "BUILD_WARN_IMPORT",
                                agent.agent_id,
                                error_msg,
                            )
                            agent.add_message("system", error_msg, "error")
                            await update_agent(agent)
                            continue  # 打回给 Agent 修复

                        # ==================== Reviewer AI 审查 (Review Phase) ====================
                        # 在部署前进行全局一致性检查
                        # 只有当 review_status 为 PASS 时才允许部署
                        if agent.metadata.get("review_status") != "PASS":
                            agent.status = SubAgentStatus.REVIEWING
                            await update_agent(agent)

                            tracer.log_event(
                                "REVIEW_ENTER",
                                agent.agent_id,
                                f"进入代码审查阶段... (轮次 {agent.review_rounds + 1}/{config.MAX_REVIEW_ROUNDS})",
                            )

                            # 记录审查开始事件
                            # 记录审查开始事件
                            tracer.log_event(
                                "REVIEW_START",
                                agent_id,
                                f"开始第 {agent.review_rounds + 1} 轮审查",
                                review_round=agent.review_rounds + 1,
                            )

                            review_passed, review_comment = await run_reviewer(
                                agent,
                                chat_key,
                                tracer,
                                previous_comment=agent.metadata.get(
                                    "last_review_comment",
                                    "",
                                ),
                            )

                            # 记录审查结果事件
                            tracer.log_event(
                                "REVIEW_RESULT",
                                agent_id,
                                f"审查{'通过' if review_passed else '拒绝'}: {review_comment[:100]}",
                                passed=review_passed,
                                review_round=agent.review_rounds + 1,
                            )

                            if review_passed:
                                tracer.log_event(
                                    "AUTO_DELIVERY",
                                    agent.agent_id,
                                    "自动交付 (无需审查)",
                                )
                                agent.metadata["review_status"] = "PASS"
                                agent.status = SubAgentStatus.WORKING  # 恢复状态
                                # 重置审查状态
                                agent.review_rounds = 0
                                agent.last_review_comment = None
                                await update_agent(agent)
                            else:
                                tracer.log_event(
                                    "REVIEW_FAIL_LOG",
                                    agent.agent_id,
                                    f"审查未通过: {review_comment}",
                                )
                                agent.status = SubAgentStatus.WORKING  # 恢复状态

                                # 更新审查失败记录
                                agent.review_rounds += 1
                                agent.last_review_comment = review_comment
                                await update_agent(agent)

                                # 检查是否达到最大审查轮次 (Fail-Open)
                                if agent.review_rounds >= config.MAX_REVIEW_ROUNDS:
                                    warning_msg = (
                                        f"⚠️ 审查已连续失败 {agent.review_rounds} 次，触发强制交付策略。\n"
                                        f"最后一次审查意见: {review_comment}\n"
                                        "请人工介入检查潜在风险。"
                                    )
                                    tracer.log_event(
                                        "REVIEW_FORCE_WARN",
                                        agent.agent_id,
                                        warning_msg,
                                    )

                                    # 强制标记为通过
                                    agent.metadata["review_status"] = "PASS"
                                    agent.metadata["review_warning"] = (
                                        warning_msg  # 记录警告供后续使用
                                    )
                                    await update_agent(agent)

                                    # 记录强制交付事件（但不 finalize，因为任务会继续）
                                    # 记录强制交付事件（但不 finalize，因为任务会继续）
                                    tracer.log_event(
                                        "FORCE_DELIVERY",
                                        agent_id,
                                        f"审查失败 {agent.review_rounds} 次，强制交付",
                                        review_rounds=agent.review_rounds,
                                        review_comment=review_comment[:200],
                                    )

                                    # 🆕 更新 99_analysis_prompt.md
                                    tracer.update_summary(
                                        new_status="FORCE_DELIVERED",
                                        additional_events=[
                                            f"{tracer.elapsed()} [FORCE_DELIVERY] {agent.agent_id}: 审查失败 {agent.review_rounds} 次，强制交付",
                                            f"{tracer.elapsed()} [LAST_REVIEW] 最后审查意见: {review_comment[:200]}...",
                                        ],
                                        error_summary=f"审查失败 {agent.review_rounds} 次后强制交付。最后问题: {review_comment[:300]}...",
                                    )

                                    # 不 continue，允许继续向下执行部署逻辑
                                else:
                                    # 发送拒绝消息给 Architect
                                    msg = (
                                        "❌ **Deployment Rejected by Reviewer AI**\n\n"
                                        "Your code compiled, but failed the global consistency review.\n"
                                        f"**Reason**:\n{review_comment}\n\n"
                                        "👉 Please fix these issues and try again."
                                    )
                                    agent.add_message(
                                        "system",
                                        msg,
                                        "feedback",
                                    )  # Feedback type ensures it's seen
                                    await update_agent(agent)
                                    continue  # ⛔ 拦截部署，进入下一轮思考

                        # 构造 Shell HTML
                        shell_html = generate_shell_html(
                            title=parsed.page_title or "Nekro App",
                            body_js=result,
                            dependencies=agent.metadata.get("dependencies", []),
                        )
                        # 部署
                        deployed_url = await deploy_html_to_worker(
                            html_content=shell_html,
                            title=parsed.page_title or f"WebApp by {agent_id}",
                            description=parsed.page_description or "",
                            template_vars={},  # 严格使用 process.env，不再支持运行时 {{var}} 替换
                        )
                        deploy_success = True

                        # 保存 VFS 快照并完成任务追踪
                        # 保存 VFS 快照并完成任务追踪
                        project_ctx = vfs.get_project_context(chat_key)
                        tracer.save_vfs_snapshot(project_ctx)
                        tracer.finalize(
                            final_status="SUCCESS",
                            error_summary="",
                        )
                    else:
                        error_msg = f"编译失败: {result}"
                        tracer.log_event("BUILD_ERROR", agent.agent_id, error_msg)

                        # 增加连续失败计数
                        agent.consecutive_failures += 1

                        # 如果连续失败超过 3 次，而且错误信息看起来是环境错误（不是语法错误），则强制停止
                        # 暂时简单处理：连续 3 次编译失败就停止
                        if agent.consecutive_failures >= 3:
                            fatal_msg = (
                                "❌ 连续多次编译失败，疑似环境配置问题或死循环。任务已强制终止以节省资源。\n"
                                f"最后一次错误: {result}"
                            )
                            tracer.log_event("BUILD_FATAL", agent.agent_id, fatal_msg)
                            await fail_agent(agent, fatal_msg)

                            # 记录任务失败
                            # 记录任务失败
                            project_ctx = vfs.get_project_context(chat_key)
                            tracer.save_vfs_snapshot(project_ctx)
                            tracer.finalize(
                                final_status="COMPILATION_FAILED",
                                error_summary=fatal_msg,
                            )

                            await send_to_main(
                                chat_key,
                                agent_id,
                                fatal_msg,
                                trigger=True,
                                tracer=tracer,
                            )
                            yield TaskCtl.fail("编译连续失败，任务终止")
                            return

                        # 核心修改: 即使编译失败也不退出，而是将错误反馈给 Agent 进行自我修复
                        # 增强错误信息：添加导出提示等帮助信息
                        enhanced_result = enhance_compile_error(result, chat_key)

                        # 添加系统消息包含错误详情
                        agent.add_message(
                            "system",
                            f"❌ Project Compilation Failed:\n{enhanced_result}\n\nPlease analyze the error and modify the code to fix it.",
                            "error",
                        )
                        await update_agent(agent)

                        # 直接 continue 进入下一次循环 (LLM 会看到错误消息并尝试修复)
                        continue

                # 2. 检查 Legacy HTML (单文件模式)
                # 只有在 VFS 为空且有 HTML 内容时才走这里
                elif html_content:
                    tracer.log_event(
                        "DEPLOY_HTML",
                        agent.agent_id,
                        "直通模式: 部署单文件 HTML",
                    )
                    deployed_url = await deploy_html_to_worker(
                        html_content=agent.render_html(html_content),
                        title=parsed.page_title or f"WebApp by {agent_id}",
                        description=parsed.page_description or "",
                        template_vars=agent.template_vars,
                    )
                    deploy_success = True

                # 更新部署状态
                if deploy_success and deployed_url:
                    tracer.log_event(
                        "DEPLOY_SUCCESS",
                        agent.agent_id,
                        f"部署成功: {deployed_url}",
                    )
                    agent.deployed_url = deployed_url
                    await update_agent(agent)
                elif deploy_success and not deployed_url:
                    # 部署函数返回 None
                    tracer.log_event(
                        "DEPLOY_FAIL",
                        agent.agent_id,
                        "部署失败 (URL check failed)",
                    )

                    # 发送部署失败通知
                    await send_to_main(
                        chat_key,
                        agent_id,
                        "❌ 部署失败: 无法将页面部署到 Cloudflare Worker (全部 3 次尝试均失败)。\n"
                        "请检查网络连接或 Access Key 配置。",
                        trigger=True,
                    )

                    await fail_agent(agent, "部署失败")
                    yield TaskCtl.fail("部署失败")
                    return

            # 只有顶层 Agent 才进入等待反馈循环
            if agent.parent_id:
                # 子 Agent 不应该到这里，但如果到了就报错
                tracer.log_event(
                    "TASK_SUCCESS_WAIT",
                    agent.agent_id,
                    "任务完成，等待用户确认",
                )
                await fail_agent(agent, "子 Agent 无产物")
                yield TaskCtl.fail("子 Agent 无产物")
                return

            # 顶层 Agent 等待反馈
            await set_status(agent, SubAgentStatus.WAITING_INPUT)

            msg = ""
            if deployed_url:
                if config.TRANSPARENT_SUB_AGENT:
                    msg = f'网页已部署: {deployed_url}\n发送反馈: send_to_webapp_agent("{agent_id}", "修改意见")\n确认: confirm_webapp_agent("{agent_id}")'
                else:
                    msg = f"网页已完成: {deployed_url}\n如需修改请告诉我。"

                # 附加审查警告 (如果是强制交付)
                review_warning = agent.metadata.get("review_warning")
                if review_warning:
                    msg += f"\n\n{review_warning}"
            elif parsed.message_to_main:
                msg = parsed.message_to_main

            if msg:
                await send_to_main(
                    chat_key,
                    agent_id,
                    msg,
                    trigger=bool(deployed_url or parsed.message_type == "question"),
                )

            yield TaskCtl.report_progress("等待反馈", 80)

            try:
                feedback: Dict[str, Any] = await handle.wait(
                    "feedback",
                    timeout=config.AGENT_TIMEOUT_MINUTES * 60,
                )
                action = feedback.get("action", "feedback")
                message = feedback.get("message", "")
                logger.info(
                    f"📨 收到反馈: action={action}, message={message[:50] if message else ''}",
                )

                if action == "confirm":
                    tracer.log_event("TASK_CONFIRMED", agent_id, "Agent 确认完成")
                    await complete_agent(agent)
                    yield TaskCtl.success("完成", data=deployed_url)
                    return

                if action == "cancel":
                    tracer.log_event(
                        "TASK_CANCELLED",
                        agent_id,
                        f"Agent 已取消: {message}",
                    )
                    agent.status = SubAgentStatus.CANCELLED
                    await update_agent(agent)
                    yield TaskCtl.cancel(message or "已取消")
                    return

                # 继续
                tracer.log_event("TASK_CONTINUE", agent_id, "Agent 继续处理反馈")
                agent.add_message("main", message, "feedback")
                await set_status(agent, SubAgentStatus.WORKING)

            except asyncio.TimeoutError:
                await fail_agent(agent, "等待超时")
                yield TaskCtl.fail("等待超时")
                return

    except Exception as e:
        logger.exception(f"Agent {agent_id} 异常: {e}")
        agent = await get_agent(agent_id, chat_key)
        if agent:
            await fail_agent(agent, str(e))
        await send_to_main(chat_key, agent_id, f"错误: {e}", trigger=True)
        yield TaskCtl.fail(str(e))


# ==================== 公开 API ====================


async def start_agent_task(
    agent_id: str,
    chat_key: str,
    tracer: TaskTracer,
) -> bool:
    """启动任务"""
    if task.is_running(TASK_TYPE, agent_id):
        return False

    # pass tracer to agent_loop
    await task.start(TASK_TYPE, agent_id, chat_key, plugin, agent_id, chat_key, tracer)
    return True


async def wake_up_agent(agent_id: str, chat_key: str, message: str = "") -> bool:
    """唤醒 Agent"""
    handle = task.get_handle(TASK_TYPE, agent_id)
    if handle:
        return handle.notify("feedback", {"action": "feedback", "message": message})

    # 如果任务未运行，需要重新创建 Tracer 并启动
    agent = await get_agent(agent_id, chat_key)
    if not agent:
        return False

    # 创建新的 Tracer (视为重启任务)
    tracer = TaskTracer(
        chat_key=chat_key,
        root_agent_id=agent_id,
        task_description=agent.task or "Resumed Agent Task",
        plugin_data_dir=str(plugin.get_plugin_data_dir()),
    )
    tracer.log_event("TASK_RESUMED", agent_id, f"任务被唤醒: {message}")

    return await start_agent_task(agent_id, chat_key, tracer)


async def confirm_agent_task(agent_id: str, chat_key: str) -> bool:
    """确认完成"""
    handle = task.get_handle(TASK_TYPE, agent_id)
    if handle:
        return handle.notify("feedback", {"action": "confirm"})
    agent = await get_agent(agent_id, chat_key)
    if agent:
        await complete_agent(agent)
    return True


async def cancel_agent_task(agent_id: str, chat_key: str, reason: str = "") -> bool:
    """取消任务"""
    handle = task.get_handle(TASK_TYPE, agent_id)
    if handle:
        return handle.notify("feedback", {"action": "cancel", "message": reason})
    agent = await get_agent(agent_id, chat_key)
    if agent:
        agent.status = SubAgentStatus.CANCELLED
        await update_agent(agent)
    return True


def _truncate_file_content(
    content: str,
    max_lines: int = 300,
    head_tail: int = 150,
) -> str:
    """截断过长文件内容，保留首尾"""
    lines = content.splitlines()
    if len(lines) <= max_lines:
        return content

    head = lines[:head_tail]
    tail = lines[-head_tail:]
    return (
        "\n".join(head)
        + f"\n\n... [Skipped {len(lines) - max_lines} lines] ...\n\n"
        + "\n".join(tail)
    )


async def run_reviewer(
    agent: WebDevAgent,
    chat_key: str,
    tracer: TaskTracer,
    previous_comment: str = "",
) -> Tuple[bool, str]:
    """运行代码审查员 (Reviewer)

    返回: (是否通过, 原因/评论)
    """
    tracer.log_event("REVIEW_START", agent.agent_id, "开始代码审查")

    # 1. 收集文件
    project_ctx = vfs.get_project_context(chat_key)
    all_files = project_ctx.list_files()

    # 过滤出代码文件
    code_extensions = (".ts", ".tsx", ".css")
    code_files = [f for f in all_files if f.endswith(code_extensions)]

    if not code_files:
        return True, "No code files to review."

    # 2. 准备内容（添加行数统计帮助 Reviewer 感知内容规模）
    file_dump = []
    for path in code_files:
        content = project_ctx.read_file(path)
        if content:
            line_count = len(content.splitlines())
            truncated = _truncate_file_content(content)
            file_dump.append(f"File: {path} ({line_count} lines)\n```\n{truncated}\n```")

    files_str = "\n\n".join(file_dump)


    # 3. 构建 Prompt
    system_prompt = reviewer.build_reviewer_prompt(agent)
    # 传入原始任务需求供审查
    user_message = reviewer.build_review_user_message(
        files_str,
        requirements=agent.task,
        previous_review_comment=previous_comment,
    )

    # 4. 调用 LLM
    # Reviewer 也是一种 specialized role，但我们这里直接调用 LLM 即可
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    model_group = (
        config.REVIEWER_MODEL_GROUP
        or config.ADVANCED_MODEL_GROUP
        or config.WEBDEV_MODEL_GROUP
    )
    try:
        mg: ModelConfigGroup = core_config.get_model_group_info(model_group)
    except Exception as e:
        tracer.log_event(
            "REVIEW_CONFIG_ERROR",
            agent.agent_id,
            f"审查 Agent 配置错误 (跳过审查): {e}",
        )
        # Fail-open: Reviewer 出错不应阻断部署
        return True, f"Reviewer Skipped: {e}"

    # 重试机制 (Max 3 times)
    for attempt in range(3):
        try:
            tracer.log_event(
                "REVIEW_ATTEMPT",
                agent.agent_id,
                f"正在审查代码... (尝试 {attempt + 1}/3)",
            )
            response = await gen_openai_chat_response(
                model=mg.CHAT_MODEL,
                api_key=mg.API_KEY,
                base_url=mg.BASE_URL,
                messages=messages,
                temperature=mg.TEMPERATURE,
            )

            content = response.response_content
            tracer.log_event(
                "REVIEW_OPINION",
                agent.agent_id,
                f"审查意见: {content[:100]}...",
            )

            # 5. 解析结果
            if '<review_result status="PASS">' in content:
                comment_match = re.search(
                    r"<comment>(.*?)</comment>",
                    content,
                    re.DOTALL,
                )
                comment = (
                    comment_match.group(1).strip() if comment_match else "Approved."
                )
                return True, comment

            if '<review_result status="FAIL">' in content:
                comment_match = re.search(
                    r"<comment>(.*?)</comment>",
                    content,
                    re.DOTALL,
                )
                comment = (
                    comment_match.group(1).strip() if comment_match else "Rejected."
                )
                return False, comment

            # 格式错误 -> 重试
            logger.warning(
                f"🧐 审查结果格式无效 (尝试 {attempt + 1}): {content[:50]}...",
            )

        except Exception as e:
            logger.error(
                f"🧐 审查流程出错 (尝试 {attempt + 1}): {e}",
            )

    # 重试耗尽，Fail-Open
    logger.warning(
        "🧐 审查多次失败，执行故障放行策略 (Fail-Open)。",
    )
    return True, "审查服务不可用 (Fail-Open)"


async def stop_agent_task(agent_id: str) -> bool:
    """停止任务"""
    return await task.cancel(TASK_TYPE, agent_id)


async def stop_all_tasks() -> int:
    """停止所有任务"""
    return await task.stop_all()
