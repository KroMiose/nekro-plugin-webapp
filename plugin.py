"""WebApp 快速部署插件配置"""

from pydantic import Field

from nekro_agent.services.plugin.base import ConfigBase, NekroPlugin

# 插件元信息
plugin = NekroPlugin(
    name="WebApp 快速部署",
    module_name="nekro_plugin_webapp",
    description="将 HTML 内容快速部署到 Cloudflare Workers 并生成在线访问链接，支持多 Agent 异步协作开发",
    version="2.0.0",
    author="KroMiose",
    url="https://github.com/KroMiose/nekro-plugin-webapp",
)


@plugin.mount_config()
class WebAppConfig(ConfigBase):
    """WebApp 部署配置"""

    # ==================== Worker 配置 ====================

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

    # ==================== 模板变量配置 ====================

    TEMPLATE_VAR_PREVIEW_LEN: int = Field(
        default=24,
        title="模板变量预览长度",
        description="子 Agent 看到的模板变量预览字符数（前后各 N 个字符）",
    )

    # ==================== HTML 预览配置 ====================

    HTML_PREVIEW_LENGTH: int = Field(
        default=12800,
        title="HTML 预览长度",
        description="子 Agent 在系统提示词中可看到的历史 HTML 代码最大字符数",
    )

    # ==================== 模型配置 ====================

    WEBDEV_MODEL_GROUP: str = Field(
        default="default",
        title="标准开发模型组",
        description="子 Agent 使用的 LLM 模型组，用于普通难度任务",
        json_schema_extra={"ref_model_groups": True, "model_type": "chat"},
    )

    ADVANCED_MODEL_GROUP: str = Field(
        default="",
        title="高级开发模型组",
        description="用于困难任务的高级 LLM 模型组，留空则始终使用标准模型",
        json_schema_extra={"ref_model_groups": True, "model_type": "chat"},
    )

    DIFFICULTY_THRESHOLD: int = Field(
        default=7,
        title="高级模型难度阈值",
        description="任务难度评分达到此值及以上时使用高级模型 (1-10)",
    )

    # ==================== 并发控制 ====================

    MAX_CONCURRENT_AGENTS_PER_CHAT: int = Field(
        default=3,
        title="单会话最大并发 Agent 数",
        description="同一会话同时运行的最大 Agent 数量",
    )

    # ==================== 历史记录 ====================

    MAX_COMPLETED_HISTORY: int = Field(
        default=10,
        title="历史任务保留数",
        description="每个会话保留的已完成任务数量",
    )

    # ==================== 迭代控制 ====================

    MAX_ITERATIONS: int = Field(
        default=10,
        title="最大迭代次数",
        description="单个 Agent 最大修改迭代次数",
    )

    # ==================== 超时控制 ====================

    AGENT_TIMEOUT_MINUTES: int = Field(
        default=10,
        title="Agent 超时时间（分钟）",
        description="Agent 无响应超时时间，超时后自动标记为失败",
    )

    AUTO_ARCHIVE_MINUTES: int = Field(
        default=60,
        title="自动归档时间（分钟）",
        description="已完成的 Agent 在无访问后自动归档的时间",
    )

    # ==================== 身份呈现配置 ====================

    TRANSPARENT_SUB_AGENT: bool = Field(
        default=True,
        title="透明展示子Agent工作",
        description="开启后，主Agent向用户表达时会明确提及助手；关闭后，主Agent会将子Agent的工作视为自己的工作",
    )


# 获取配置实例
config: WebAppConfig = plugin.get_config(WebAppConfig)

# 获取插件存储
store = plugin.store
