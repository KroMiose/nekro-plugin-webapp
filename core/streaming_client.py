"""流式文本客户端 (Streaming Text Client)

提供纯文本模式的流式 LLM 调用。
Text-to-Tool Bridge 架构的底层通信组件。

注意: 此版本移除了所有 Native Tool Call 支持。
工具调用通过文本解析实现 (@@TOOL 标记)。
"""

from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from openai import AsyncOpenAI

from nekro_agent.core import config as core_config
from nekro_agent.core.logger import logger


def _create_http_client(
    proxy_url: Optional[str] = None,
    read_timeout: int = 300,
    write_timeout: int = 300,
    connect_timeout: int = 30,
    pool_timeout: int = 30,
) -> httpx.AsyncClient:
    """创建配置好的 httpx.AsyncClient

    复用 nekro-agent 的 HTTP 客户端配置模式。
    """

    async def enforce_user_agent(request: httpx.Request) -> None:
        request.headers["User-Agent"] = core_config.OPENAI_CLIENT_USER_AGENT

    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=write_timeout,
            pool=pool_timeout,
        ),
        proxies={"http://": proxy_url, "https://": proxy_url} if proxy_url else None,
        event_hooks={"request": [enforce_user_agent]},
    )


async def stream_text_completion(
    messages: List[Dict[str, Any]],
    model_group: str,
    proxy_url: Optional[str] = None,
) -> AsyncIterator[str]:
    """流式调用 OpenAI 兼容 API（纯文本模式）

    使用 nekro-agent 的模型组配置获取 API 参数。
    不传递 tools 参数，LLM 只输出纯文本。

    Args:
        messages: 消息列表
        model_group: 模型组名称
        proxy_url: 可选代理 URL

    Yields:
        str: 文本内容增量

    Example:
        async for chunk in stream_text_completion(messages, "default"):
            print(chunk, end="", flush=True)
    """
    # 获取模型配置
    model_info = core_config.get_model_group_info(model_group)

    logger.info(
        f"[StreamingClient] 开始流式调用 (纯文本模式)，"
        f"模型组: {model_group}, 模型: {model_info.CHAT_MODEL}",
    )

    http_client = _create_http_client(proxy_url=proxy_url)

    try:
        async with AsyncOpenAI(
            api_key=model_info.API_KEY.strip() if model_info.API_KEY else None,
            base_url=model_info.BASE_URL,
            http_client=http_client,
        ) as client:
            stream = await client.chat.completions.create(
                model=model_info.CHAT_MODEL,
                messages=messages,  # type: ignore[arg-type]
                temperature=model_info.TEMPERATURE,
                stream=True,
                # 不传递 tools 参数，纯文本模式
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

    except Exception as e:
        logger.exception(f"[StreamingClient] 流式调用异常: {e}")
        raise
    finally:
        await http_client.aclose()


# ==================== 保留旧接口以便渐进式迁移 ====================
# 以下代码在迁移完成后可以删除

from dataclasses import dataclass, field


@dataclass
class ToolCallDelta:
    """Tool Call 增量数据 (已废弃，保留兼容)"""

    index: int
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    arguments_delta: str = ""


@dataclass
class StreamChunk:
    """流式响应块 (已废弃，保留兼容)"""

    content_delta: Optional[str] = None
    tool_calls: List[ToolCallDelta] = field(default_factory=list)
    finish_reason: Optional[str] = None


async def stream_tool_call_completion(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],  # noqa: ARG001 - 保留兼容性
    model_group: str,
    proxy_url: Optional[str] = None,
) -> AsyncIterator[StreamChunk]:
    """流式调用 (已废弃，保留兼容)

    WARNING: 此函数已废弃，将在迁移完成后删除。
    请使用 stream_text_completion() 替代。
    """
    logger.warning(
        "[StreamingClient] stream_tool_call_completion 已废弃，"
        "请迁移到 stream_text_completion",
    )

    # 简化实现：忽略 tools 参数，只返回文本
    async for text in stream_text_completion(messages, model_group, proxy_url):
        yield StreamChunk(content_delta=text)
