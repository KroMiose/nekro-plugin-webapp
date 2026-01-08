"""子 Agent 工作循环

负责子 Agent 的 LLM 调用、响应解析和状态更新。
"""

import asyncio
import contextlib
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from nekro_agent.api.core import config as core_config
from nekro_agent.api.core import logger
from nekro_agent.services.agent.creator import OpenAIChatMessage
from nekro_agent.services.agent.openai import gen_openai_chat_response

from ..models import AgentStatus, MessageType, WebDevAgent, WebDevResponse
from ..plugin import config
from .agent_pool import (
    fail_agent,
    get_agent,
    update_agent,
    update_agent_deployed_url,
    update_agent_html,
    update_agent_progress,
    update_agent_status,
)
from .deploy import deploy_html_to_worker
from .message_bus import notify_main_agent

# 正在运行的 Agent 任务: (agent_id, chat_key) -> Task
_running_tasks: dict[tuple[str, str], asyncio.Task] = {}


@dataclass
class SearchReplaceBlock:
    """搜索替换块"""

    search: str  # 要搜索的原始内容
    replace: str  # 替换后的新内容


def parse_search_replace_blocks(raw_response: str) -> List[SearchReplaceBlock]:
    """解析响应中的 Search/Replace 块

    格式:
    <<<<<<< SEARCH
    要替换的原始内容
    =======
    替换后的新内容
    >>>>>>> REPLACE

    Args:
        raw_response: LLM 原始响应

    Returns:
        SearchReplaceBlock 列表
    """
    blocks: List[SearchReplaceBlock] = []

    # 匹配 <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE
    pattern = re.compile(
        r"<{7}\s*SEARCH\s*\n(.*?)\n={7}\s*\n(.*?)\n>{7}\s*REPLACE",
        re.DOTALL,
    )

    for match in pattern.finditer(raw_response):
        search_content = match.group(1)
        replace_content = match.group(2)

        if search_content:
            blocks.append(
                SearchReplaceBlock(
                    search=search_content,
                    replace=replace_content,
                ),
            )

    return blocks


def apply_search_replace_blocks(
    html: str,
    blocks: List[SearchReplaceBlock],
) -> Tuple[str, List[str]]:
    """应用搜索替换块

    Args:
        html: 原始 HTML 内容
        blocks: 搜索替换块列表

    Returns:
        (修改后的 HTML, 错误消息列表)
    """
    if not blocks:
        return html, []

    errors: List[str] = []
    result = html

    for i, block in enumerate(blocks, 1):
        if block.search in result:
            # 精确匹配成功
            result = result.replace(block.search, block.replace, 1)
        else:
            # 尝试忽略首尾空白的模糊匹配
            search_stripped = block.search.strip()
            if search_stripped and search_stripped in result:
                result = result.replace(search_stripped, block.replace.strip(), 1)
            else:
                preview = block.search[:60].replace("\n", "\\n")
                errors.append(f"块 {i}: 未找到匹配内容 '{preview}...'")

    return result, errors


def _get_model_groups_with_fallback(difficulty: int) -> List[str]:
    """获取模型组列表（含降级）

    Args:
        difficulty: 任务难度

    Returns:
        模型组名称列表，按优先级排序（高级模型在前，标准模型在后）
    """
    models = []
    # 如果配置了高级模型且难度达到阈值，优先使用高级模型
    if config.ADVANCED_MODEL_GROUP and difficulty >= config.DIFFICULTY_THRESHOLD:
        models.append(config.ADVANCED_MODEL_GROUP)
    # 标准模型作为降级选项
    if config.WEBDEV_MODEL_GROUP and config.WEBDEV_MODEL_GROUP not in models:
        models.append(config.WEBDEV_MODEL_GROUP)
    return models


async def _call_llm_with_fallback(
    messages: List[OpenAIChatMessage],
    model_groups: List[str],
    agent_id: str,
) -> Tuple[Optional[str], Optional[str]]:
    """调用 LLM，支持自动降级

    Args:
        messages: 消息列表
        model_groups: 模型组列表（按优先级排序）
        agent_id: Agent ID（用于日志）

    Returns:
        (响应内容, 错误信息) - 成功时错误信息为 None
    """
    last_error: Optional[str] = None

    for i, model_group_name in enumerate(model_groups):
        is_fallback = i > 0
        try:
            model_group = core_config.get_model_group_info(model_group_name)

            if is_fallback:
                logger.warning(f"Agent {agent_id} 降级到模型: {model_group.CHAT_MODEL}")
            else:
                logger.info(
                    f"Agent {agent_id} 调用 LLM，模型: {model_group.CHAT_MODEL}",
                )

            response = await gen_openai_chat_response(
                model=model_group.CHAT_MODEL,
                messages=messages,
                temperature=model_group.TEMPERATURE,
                top_p=model_group.TOP_P,
                top_k=model_group.TOP_K,
                frequency_penalty=model_group.FREQUENCY_PENALTY,
                presence_penalty=model_group.PRESENCE_PENALTY,
                extra_body=model_group.EXTRA_BODY,
                base_url=model_group.BASE_URL,
                api_key=model_group.API_KEY,
                stream_mode=False,
                proxy_url=model_group.CHAT_PROXY,
            )
        except Exception as e:
            error_msg = str(e)
            last_error = f"模型 {model_group_name} 调用失败: {error_msg}"
            logger.error(f"Agent {agent_id} {last_error}")

            # 如果还有降级选项，记录日志
            has_more = i < len(model_groups) - 1
            if has_more:
                logger.info(f"Agent {agent_id} 尝试降级到下一个模型...")
        else:
            # 调用成功
            return response.response_content, None

    # 所有模型都失败了
    return None, last_error


async def run_webdev_agent_loop(agent_id: str, chat_key: str) -> None:
    """子 Agent 异步工作循环

    Args:
        agent_id: Agent ID
        chat_key: 会话 key
    """
    agent = await get_agent(agent_id, chat_key)
    if not agent:
        logger.error(f"Agent {agent_id} 不存在")
        return

    if not agent.is_active():
        logger.warning(f"Agent {agent_id} 已不在活跃状态")
        return

    logger.info(f"启动 WebDev Agent 工作循环: {agent_id}")

    try:
        # 更新状态为思考中
        await update_agent_status(agent_id, chat_key, AgentStatus.THINKING)
        agent.start_time = int(time.time())
        await update_agent(agent)

        # 构建消息并调用 LLM
        from ..prompts.webdev_system import build_webdev_messages

        messages = build_webdev_messages(agent)

        # 获取模型列表（含降级）
        model_groups = _get_model_groups_with_fallback(agent.difficulty)

        await update_agent_status(agent_id, chat_key, AgentStatus.CODING)

        # 调用 LLM（自动降级）
        raw_content, llm_error = await _call_llm_with_fallback(
            messages,
            model_groups,
            agent_id,
        )

        if llm_error:
            # 所有模型都失败，通知主 Agent
            await fail_agent(agent_id, chat_key, llm_error)
            await notify_main_agent(
                agent_id=agent_id,
                chat_key=chat_key,
                message=f"LLM 调用失败: {llm_error}\n请检查模型配置或稍后重试。",
                msg_type=MessageType.RESULT,
                trigger=True,
            )
            return

        if not raw_content:
            await fail_agent(agent_id, chat_key, "LLM 返回空响应")
            await notify_main_agent(
                agent_id=agent_id,
                chat_key=chat_key,
                message="LLM 返回空响应，请重试。",
                msg_type=MessageType.RESULT,
                trigger=True,
            )
            return
        logger.debug(f"Agent {agent_id} LLM 响应长度: {len(raw_content)}")

        # 解析响应
        parsed = parse_webdev_response(raw_content)

        # 更新进度
        if parsed.progress_percent > 0 or parsed.current_step:
            await update_agent_progress(
                agent_id,
                chat_key,
                parsed.progress_percent,
                parsed.current_step,
            )

        # 更新 Agent 对话历史（添加自己的回复）
        agent = await get_agent(agent_id, chat_key)
        if agent:
            agent.add_message(
                msg_type=MessageType.PROGRESS,
                sender="webdev",
                content=raw_content[:500] + "..."
                if len(raw_content) > 500
                else raw_content,
            )
            agent.iteration_count += 1
            await update_agent(agent)

        # 处理 HTML 代码（完整代码或增量编辑）
        html_to_deploy: Optional[str] = None
        edit_applied = False

        if parsed.html_content:
            # 有完整代码块，直接使用
            html_to_deploy = parsed.html_content
        else:
            # 尝试解析 Search/Replace 块
            blocks = parse_search_replace_blocks(raw_content)
            if blocks:
                # 获取当前 HTML
                agent = await get_agent(agent_id, chat_key)
                if agent and agent.current_html:
                    html_to_deploy, edit_errors = apply_search_replace_blocks(
                        agent.current_html,
                        blocks,
                    )
                    edit_applied = True
                    if edit_errors:
                        logger.warning(f"Agent {agent_id} 编辑应用警告: {edit_errors}")
                else:
                    logger.warning(f"Agent {agent_id} 尝试增量编辑但无现有 HTML")

        if html_to_deploy:
            await update_agent_html(
                agent_id,
                chat_key,
                html_to_deploy,
                parsed.page_title,
                parsed.page_description,
            )

            # 部署到 Worker
            await update_agent_status(agent_id, chat_key, AgentStatus.DEPLOYING)

            title = parsed.page_title or f"WebApp by {agent_id}"
            description = parsed.page_description or "Generated by WebDev Agent"

            # 获取最新的 agent 数据（包含模板变量）
            agent = await get_agent(agent_id, chat_key)
            template_vars = agent.template_vars if agent else {}

            deployed_url = await deploy_html_to_worker(
                html_content=html_to_deploy,
                title=title,
                description=description,
                template_vars=template_vars,
            )

            if deployed_url:
                await update_agent_deployed_url(agent_id, chat_key, deployed_url)
                await update_agent_status(
                    agent_id,
                    chat_key,
                    AgentStatus.WAITING_FEEDBACK,
                )

                # 根据身份呈现模式构建通知文案
                edit_info = "（增量更新）" if edit_applied else ""
                if config.TRANSPARENT_SUB_AGENT:
                    # 透明式
                    message = f'网页已部署完成{edit_info}！\n预览链接: {deployed_url}\n\n如需修改，请使用 send_to_webapp_agent("{agent_id}", "修改意见") 发送反馈。\n确认完成请使用 confirm_webapp_agent("{agent_id}")。'
                else:
                    # 沉浸式
                    message = f"我已完成网页开发{edit_info}！\n预览链接: {deployed_url}\n\n如需调整，请告诉我你的修改意见。"

                await notify_main_agent(
                    agent_id=agent_id,
                    chat_key=chat_key,
                    message=message,
                    msg_type=MessageType.RESULT,
                    trigger=True,
                )
            else:
                await fail_agent(agent_id, chat_key, "部署到 Worker 失败")
                await notify_main_agent(
                    agent_id=agent_id,
                    chat_key=chat_key,
                    message="网页部署失败，请检查 Worker 配置。",
                    msg_type=MessageType.RESULT,
                    trigger=True,
                )

        # 处理向主 Agent 发送的消息
        elif parsed.message_to_main:
            msg_type = parsed.message_type or MessageType.PROGRESS

            if msg_type == MessageType.QUESTION:
                await update_agent_status(
                    agent_id,
                    chat_key,
                    AgentStatus.WAITING_FEEDBACK,
                )

            await notify_main_agent(
                agent_id=agent_id,
                chat_key=chat_key,
                message=parsed.message_to_main,
                msg_type=msg_type,
                trigger=msg_type == MessageType.QUESTION,  # 只有问题才触发主 Agent
            )

        else:
            # 没有 HTML 也没有消息，可能需要继续工作
            logger.warning(f"Agent {agent_id} 响应中没有找到代码或消息")

    except Exception as e:
        logger.exception(f"Agent {agent_id} 工作循环异常: {e}")
        await fail_agent(agent_id, chat_key, str(e))
        await notify_main_agent(
            agent_id=agent_id,
            chat_key=chat_key,
            message=f"开发过程中发生错误: {e}",
            msg_type=MessageType.RESULT,
            trigger=True,
        )


def parse_webdev_response(raw_response: str) -> WebDevResponse:
    """解析子 Agent 的响应

    Args:
        raw_response: 原始 LLM 响应

    Returns:
        解析后的响应对象
    """
    result = WebDevResponse(raw_response=raw_response)

    # 解析 <status> 块
    status_match = re.search(
        r"<status>\s*progress:\s*(\d+)\s*step:\s*[\"']?([^\"'\n<]+)[\"']?\s*</status>",
        raw_response,
        re.DOTALL | re.IGNORECASE,
    )
    if status_match:
        result.progress_percent = int(status_match.group(1))
        result.current_step = status_match.group(2).strip()

    # 解析 <message> 块
    message_match = re.search(
        r'<message\s+type=["\']?(question|progress|result)["\']?\s*>(.*?)</message>',
        raw_response,
        re.DOTALL | re.IGNORECASE,
    )
    if message_match:
        msg_type_str = message_match.group(1).lower()
        result.message_to_main = message_match.group(2).strip()
        result.message_type = {
            "question": MessageType.QUESTION,
            "progress": MessageType.PROGRESS,
            "result": MessageType.RESULT,
        }.get(msg_type_str, MessageType.PROGRESS)

    # 解析 <code> 块中的 HTML
    code_match = re.search(r"<code>\s*(.*?)\s*</code>", raw_response, re.DOTALL)
    if code_match:
        html_content = code_match.group(1).strip()

        # 提取标题和描述注释
        title_match = re.search(r"<!--\s*TITLE:\s*(.+?)\s*-->", html_content)
        desc_match = re.search(r"<!--\s*DESC:\s*(.+?)\s*-->", html_content)

        if title_match:
            result.page_title = title_match.group(1).strip()
        if desc_match:
            result.page_description = desc_match.group(1).strip()

        # 清理注释后的 HTML
        html_content = re.sub(r"<!--\s*TITLE:.*?-->", "", html_content)
        html_content = re.sub(r"<!--\s*DESC:.*?-->", "", html_content)
        result.html_content = html_content.strip()

    # 如果没有 <code> 块，尝试直接提取 HTML
    if not result.html_content:
        # 尝试匹配 ```html ... ``` 代码块
        html_block_match = re.search(r"```html\s*(.*?)\s*```", raw_response, re.DOTALL)
        if html_block_match:
            html_content = html_block_match.group(1).strip()

            # 提取标题和描述
            title_match = re.search(r"<!--\s*TITLE:\s*(.+?)\s*-->", html_content)
            desc_match = re.search(r"<!--\s*DESC:\s*(.+?)\s*-->", html_content)

            if title_match:
                result.page_title = title_match.group(1).strip()
            if desc_match:
                result.page_description = desc_match.group(1).strip()

            # 清理注释
            html_content = re.sub(r"<!--\s*TITLE:.*?-->", "", html_content)
            html_content = re.sub(r"<!--\s*DESC:.*?-->", "", html_content)
            result.html_content = html_content.strip()

    # 如果还是没有，尝试直接查找完整的 HTML 文档
    if not result.html_content:
        html_doc_match = re.search(
            r"(<!DOCTYPE html>.*?</html>)",
            raw_response,
            re.DOTALL | re.IGNORECASE,
        )
        if html_doc_match:
            result.html_content = html_doc_match.group(1).strip()

            # 从 <title> 标签提取标题
            if result.html_content:
                title_tag_match = re.search(
                    r"<title>(.+?)</title>",
                    result.html_content,
                    re.IGNORECASE,
                )
                if title_tag_match and not result.page_title:
                    result.page_title = title_tag_match.group(1).strip()

    return result


async def start_agent_task(agent_id: str, chat_key: str) -> bool:
    """启动 Agent 任务

    Args:
        agent_id: Agent ID
        chat_key: 会话 key

    Returns:
        是否成功启动
    """
    task_key = (agent_id, chat_key)
    if task_key in _running_tasks:
        task = _running_tasks[task_key]
        if not task.done():
            logger.warning(f"Agent {agent_id} 任务已在运行中")
            return False

    task = asyncio.create_task(run_webdev_agent_loop(agent_id, chat_key))
    _running_tasks[task_key] = task

    # 清理已完成的任务
    _cleanup_finished_tasks()

    return True


async def wake_up_agent(agent_id: str, chat_key: str) -> bool:
    """唤醒 Agent 继续工作

    当主 Agent 发送消息后调用此方法唤醒子 Agent

    Args:
        agent_id: Agent ID
        chat_key: 会话 key

    Returns:
        是否成功唤醒
    """
    agent = await get_agent(agent_id, chat_key)
    if not agent:
        logger.error(f"Agent {agent_id} 不存在，无法唤醒")
        return False

    if not agent.is_active():
        logger.warning(f"Agent {agent_id} 已不在活跃状态，无法唤醒")
        return False

    # 检查迭代次数
    if agent.iteration_count >= config.MAX_ITERATIONS:
        await fail_agent(
            agent_id,
            chat_key,
            f"已达到最大迭代次数 ({config.MAX_ITERATIONS})",
        )
        return False

    # 启动新的工作循环
    return await start_agent_task(agent_id, chat_key)


def _cleanup_finished_tasks() -> None:
    """清理已完成的任务"""
    finished = [key for key, task in _running_tasks.items() if task.done()]
    for key in finished:
        del _running_tasks[key]


async def stop_agent_task(agent_id: str, chat_key: str) -> bool:
    """停止 Agent 任务

    Args:
        agent_id: Agent ID
        chat_key: 会话 key

    Returns:
        是否成功停止
    """
    task_key = (agent_id, chat_key)
    if task_key not in _running_tasks:
        return False

    task = _running_tasks[task_key]
    if not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    del _running_tasks[task_key]
    return True


async def stop_all_tasks() -> int:
    """停止所有正在运行的 Agent 任务

    用于插件清理时调用。

    Returns:
        停止的任务数量
    """
    count = 0
    for (_agent_id, _chat_key), task in list(_running_tasks.items()):
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            count += 1
    _running_tasks.clear()
    return count


def get_running_task_keys() -> List[Tuple[str, str]]:
    """获取当前正在运行的任务列表

    Returns:
        (agent_id, chat_key) 元组列表
    """
    _cleanup_finished_tasks()
    return list(_running_tasks.keys())
