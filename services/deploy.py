"""部署服务

负责将 HTML 内容部署到 Cloudflare Worker。
"""

import asyncio
from typing import Dict, Optional

import httpx

from nekro_agent.api.core import logger

from ..models import CreatePageRequest, CreatePageResponse
from ..plugin import config


def render_template_vars(html_content: str, template_vars: Dict[str, str]) -> str:
    """渲染模板变量，替换 {{key}} 占位符

    Args:
        html_content: 原始 HTML 内容
        template_vars: 模板变量字典

    Returns:
        替换后的 HTML 内容
    """
    result = html_content
    for key, value in template_vars.items():
        placeholder = "{{" + key + "}}"
        result = result.replace(placeholder, value)
    return result


async def deploy_html_to_worker(
    html_content: str,
    title: str,
    description: str,
    expires_in_days: int = 30,
    template_vars: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """部署 HTML 到 Worker

    Args:
        html_content: HTML 内容
        title: 页面标题
        description: 页面描述
        expires_in_days: 过期天数
        template_vars: 模板变量（用于替换 {{key}} 占位符）

    Returns:
        部署后的 URL，失败返回 None
    """
    # 渲染模板变量
    if template_vars:
        html_content = render_template_vars(html_content, template_vars)
        logger.debug(f"已替换 {len(template_vars)} 个模板变量")
    if not config.WORKER_URL:
        logger.error("未配置 Worker URL")
        return None

    if not config.ACCESS_KEY:
        logger.error("未配置访问密钥")
        return None

    worker_url = config.WORKER_URL.rstrip("/")
    api_url = f"{worker_url}/api/pages"

    # 确保 description 非空
    if not description or not description.strip():
        description = title or "WebApp Page"
        logger.debug(f"description 为空，使用默认值: {description}")

    request_data = CreatePageRequest(
        title=title,
        description=description,
        html_content=html_content,
        expires_in_days=expires_in_days,
    )

    max_retries = 3
    base_delay = 1.0

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    api_url,
                    json=request_data.model_dump(),
                    headers={
                        "Authorization": f"Bearer {config.ACCESS_KEY}",
                    },
                )

                if response.status_code in (200, 201):
                    data = response.json()
                    result = CreatePageResponse.model_validate(data)
                    logger.info(f"部署成功: {result.url}")
                    return result.url

                logger.warning(
                    f"部署尝试 {attempt + 1}/{max_retries} 失败: HTTP {response.status_code}, {response.text}",
                )

        except httpx.TimeoutException:
            logger.warning(f"部署尝试 {attempt + 1}/{max_retries} 超时")
        except Exception as e:
            logger.warning(f"部署尝试 {attempt + 1}/{max_retries} 异常: {e}")

        # 如果不是最后一次尝试，等待后重试
        if attempt < max_retries - 1:
            delay = base_delay * (2**attempt)
            logger.info(f"等待 {delay}秒后重试...")
            await asyncio.sleep(delay)

    logger.error("部署最终失败")
    return None


async def check_worker_health() -> bool:
    """检查 Worker 健康状态

    Returns:
        是否健康
    """
    if not config.WORKER_URL:
        return False

    worker_url = config.WORKER_URL.rstrip("/")
    health_url = f"{worker_url}/health"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(health_url)
            return response.status_code == 200
    except Exception:
        return False

