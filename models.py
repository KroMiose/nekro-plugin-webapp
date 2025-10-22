"""数据模型定义"""

from pydantic import BaseModel, Field


class CreatePageRequest(BaseModel):
    """创建页面请求"""

    title: str = Field(..., min_length=1, max_length=200, description="页面标题")
    description: str = Field(..., min_length=1, max_length=1000, description="页面描述")
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


class ApiKeyInfo(BaseModel):
    """API 密钥信息"""

    key_id: str = Field(..., description="密钥 ID")
    key_name: str = Field(..., description="密钥名称")
    created_at: int = Field(..., description="创建时间戳")
    expires_at: int | None = Field(default=None, description="过期时间戳")
    is_active: bool = Field(default=True, description="是否活跃")
    usage_count: int = Field(default=0, description="使用次数")
    max_pages: int = Field(default=100, description="最大页面数")
    permissions: str = Field(default="create,view", description="权限列表")


class WorkerHealthResponse(BaseModel):
    """Worker 健康检查响应"""

    status: str = Field(..., description="状态")
    timestamp: int = Field(..., description="时间戳")


class WorkerStats(BaseModel):
    """Worker 统计信息"""

    pages_count: int = Field(default=0, description="页面总数")
    keys_count: int = Field(default=0, description="密钥总数")
    total_access: int = Field(default=0, description="总访问次数")
