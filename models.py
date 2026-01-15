"""数据模型定义

精简版：使用框架 SubAgent 基类，删除冗余模型。
"""

import time
from typing import Dict, Optional

from pydantic import BaseModel, Field

from .agent_core import SubAgent

# ==================== API 相关模型 ====================


class CreatePageRequest(BaseModel):
    """创建页面请求"""

    title: str = Field(..., min_length=1, max_length=200, description="页面标题")
    description: str = Field(default="", max_length=1000, description="页面描述")
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


class WorkerHealthResponse(BaseModel):
    """Worker 健康检查响应"""

    status: str = Field(..., description="状态")
    timestamp: int = Field(..., description="时间戳")
    initialized: bool = Field(default=False, description="是否已初始化管理密钥")


# ==================== WebDev Agent 模型 ====================


class WebDevAgent(SubAgent):
    """网页开发 Agent

    继承框架 SubAgent，添加网页开发专有字段。
    """

    # === 网页产出 ===
    current_html: Optional[str] = Field(default=None, description="当前 HTML")
    deployed_url: Optional[str] = Field(default=None, description="部署 URL")
    page_title: Optional[str] = Field(default=None, description="页面标题")
    page_description: Optional[str] = Field(default=None, description="页面描述")

    # === 开发配置 ===
    difficulty: int = Field(default=3, ge=1, le=5, description="难度 1-5")
    template_vars: Dict[str, str] = Field(default_factory=dict, description="模板变量")

    # === 时间追踪 ===
    start_time: Optional[int] = Field(default=None, description="开始时间")
    confirmed_time: Optional[int] = Field(default=None, description="确认时间")
    complete_time: Optional[int] = Field(default=None, description="完成时间")
    last_access_time: int = Field(
        default_factory=lambda: int(time.time()),
        description="访问时间",
    )

    # === 迭代控制 ===
    iteration_count: int = Field(default=0, description="迭代次数")
    review_rounds: int = Field(default=0, description="当前审查轮次")
    last_review_comment: Optional[str] = Field(default=None, description="上一次审查意见")
    error_message: Optional[str] = Field(default=None, description="错误信息")
    consecutive_failures: int = Field(default=0, description="连续失败次数")

    # === 流式统计 ===
    stream_chars: int = Field(default=0, description="当前流式输出字符数")
    stream_start_time: Optional[float] = Field(default=None, description="流式开始时间")
    stream_last_update: Optional[float] = Field(default=None, description="最后更新时间")
    total_chars_generated: int = Field(default=0, description="总生成字符数")

    def touch(self) -> None:
        """更新访问时间"""
        self.last_access_time = int(time.time())

    def set_template_var(self, key: str, value: str) -> None:
        """设置模板变量"""
        self.template_vars[key] = value
        self.updated_at = int(time.time())

    def delete_template_var(self, key: str) -> bool:
        """删除模板变量"""
        if key in self.template_vars:
            del self.template_vars[key]
            self.updated_at = int(time.time())
            return True
        return False

    def get_template_preview(self, key: str, max_len: int = 50) -> str:
        """获取模板变量预览"""
        value = self.template_vars.get(key, "")
        if not value:
            return "(空)"
        if len(value) <= max_len * 2:
            return value
        return f"{value[:max_len]}...({len(value)}字符)...{value[-max_len:]}"

    def render_html(self, html: str) -> str:
        """渲染 HTML，替换模板变量"""
        result = html
        for key, value in self.template_vars.items():
            result = result.replace("{{" + key + "}}", value)
        return result


# ==================== 响应解析模型 ====================


class WebDevResponse(BaseModel):
    """LLM 响应解析结果"""

    progress_percent: int = Field(default=0)
    current_step: str = Field(default="")
    message_to_main: Optional[str] = Field(default=None)
    message_type: Optional[str] = Field(default=None)  # question, progress, result
    html_content: Optional[str] = Field(default=None)
    page_title: Optional[str] = Field(default=None)
    page_description: Optional[str] = Field(default=None)
    raw_response: str = Field(default="")
