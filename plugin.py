"""WebApp 快速部署插件配置"""

from pydantic import Field

from nekro_agent.services.plugin.base import ConfigBase, NekroPlugin

# 插件元信息
plugin = NekroPlugin(
    name="WebApp 快速部署",
    module_name="nekro_plugin_webapp",
    description="将 HTML 内容快速部署到 Cloudflare Workers 并生成在线访问链接",
    version="1.0.0",
    author="KroMiose",
    url="https://github.com/KroMiose/nekro-plugin-webapp",
)


@plugin.mount_config()
class WebAppConfig(ConfigBase):
    """WebApp 部署配置"""

    WORKER_URL: str = Field(
        default="",
        title="Worker 访问地址",
        description="Cloudflare Worker 的完整 URL (如: https://your-worker.workers.dev)",
    )

    ACCESS_KEY: str = Field(
        default="",
        title="访问密钥",
        description="用于创建页面的访问密钥（需在管理界面创建）",
        json_schema_extra={"is_secret": True},
    )

    ENABLE_BASE64_IMAGES: bool = Field(
        default=False,
        title="允许 Base64 图片嵌入",
        description=(
            "开启后，AI 将会被指导如何在 HTML 中使用 Base64 编码嵌入图片。"
            "注意：Base64 图片会显著增加页面体积，请确保 Worker 配置了足够的页面大小限制（建议至少 5MB）"
        ),
    )


# 获取配置实例
config: WebAppConfig = plugin.get_config(WebAppConfig)
