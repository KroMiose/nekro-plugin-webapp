"""WebApp 快速部署插件配置"""

from typing import Literal

from pydantic import Field

from nekro_agent.api import i18n
from nekro_agent.api.plugin import ConfigBase, ExtraField, NekroPlugin

# 插件元信息
plugin = NekroPlugin(
    name="WebApp 多层级智能体协作",
    module_name="nekro_plugin_webapp",
    description="智能编排｜多层级智能体自动组织嵌套｜AI 自主派遣架构师，按需动态创建子智能体，支持无限嵌套的团队组织结构。后台异步工作不阻塞主对话，自动完成编码、审查、编译到全球部署的完整流程。",
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
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="Worker 访问地址",
                en_US="Worker URL",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="Cloudflare Worker 的完整 URL (如: https://your-worker.workers.dev)",
                en_US="Full URL of Cloudflare Worker (e.g., https://your-worker.workers.dev)",
            ),
        ).model_dump(),
    )

    ACCESS_KEY: str = Field(
        default="",
        title="访问密钥",
        description="用于创建页面的访问密钥（需在管理界面创建）",
        json_schema_extra=ExtraField(
            is_secret=True,
            i18n_title=i18n.i18n_text(
                zh_CN="访问密钥",
                en_US="Access Key",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="用于创建页面的访问密钥（需在管理界面创建）",
                en_US="Access key for creating pages (create in management interface)",
            ),
        ).model_dump(),
    )

    # ==================== 模板变量配置 ====================

    TEMPLATE_VAR_PREVIEW_LEN: int = Field(
        default=24,
        title="模板变量预览长度",
        description="子 Agent 看到的模板变量预览字符数（前后各 N 个字符）",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="模板变量预览长度",
                en_US="Template Variable Preview Length",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="子 Agent 看到的模板变量预览字符数（前后各 N 个字符）",
                en_US="Number of characters of template variable preview seen by sub-agents (N characters before and after)",
            ),
        ).model_dump(),
    )

    # ==================== HTML 预览配置 ====================

    HTML_PREVIEW_LENGTH: int = Field(
        default=12800,
        title="HTML 预览长度",
        description="子 Agent 在系统提示词中可看到的历史 HTML 代码最大字符数",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="HTML 预览长度",
                en_US="HTML Preview Length",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="子 Agent 在系统提示词中可看到的历史 HTML 代码最大字符数",
                en_US="Maximum characters of historical HTML code visible to sub-agents in system prompt",
            ),
        ).model_dump(),
    )

    # ==================== 模型配置 ====================

    WEBDEV_MODEL_GROUP: str = Field(
        default="default",
        title="标准开发模型组",
        description="子 Agent 使用的 LLM 模型组，用于普通难度任务",
        json_schema_extra=ExtraField(
            ref_model_groups=True,
            model_type="chat",
            i18n_title=i18n.i18n_text(
                zh_CN="标准开发模型组",
                en_US="Standard Dev Model Group",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="子 Agent 使用的 LLM 模型组，用于普通难度任务",
                en_US="LLM model group used by sub-agents for normal difficulty tasks",
            ),
        ).model_dump(),
    )

    ADVANCED_MODEL_GROUP: str = Field(
        default="",
        title="高级开发模型组",
        description="用于困难任务的高级 LLM 模型组，留空则始终使用标准模型",
        json_schema_extra=ExtraField(
            ref_model_groups=True,
            model_type="chat",
            i18n_title=i18n.i18n_text(
                zh_CN="高级开发模型组",
                en_US="Advanced Dev Model Group",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="用于困难任务的高级 LLM 模型组，留空则始终使用标准模型",
                en_US="Advanced LLM model group for hard tasks, leave empty to always use standard model",
            ),
        ).model_dump(),
    )

    REVIEWER_MODEL_GROUP: str = Field(
        default="",
        title="代码审查模型组",
        description="Reviewer Agent 使用的 LLM 模型组，建议使用大上下文的模型。留空则通过回退逻辑自动选择。",
        json_schema_extra=ExtraField(
            ref_model_groups=True,
            model_type="chat",
            i18n_title=i18n.i18n_text(
                zh_CN="代码审查模型组",
                en_US="Code Review Model Group",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="Reviewer Agent 使用的 LLM 模型组，建议使用大上下文的模型。留空则通过回退逻辑自动选择。",
                en_US="LLM model group for Reviewer Agent, recommending large context models. Leave empty to auto-select via fallback logic.",
            ),
        ).model_dump(),
    )

    DIFFICULTY_THRESHOLD: int = Field(
        default=4,
        title="高级模型难度阈值",
        description="任务难度评分达到此值及以上时使用高级模型 (1-5)",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="高级模型难度阈值",
                en_US="Advanced Model Difficulty Threshold",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="任务难度评分达到此值及以上时使用高级模型 (1-5)",
                en_US="Use advanced model when task difficulty score reaches this value or above (1-5)",
            ),
        ).model_dump(),
    )

    REVIEW_STANDARD: Literal["strict", "standard", "lenient"] = Field(
        default="standard",
        title="审查标准",
        description="strict: 严格(代码+业务), standard: 标准(代码+核心业务), lenient: 宽松(仅代码)。注意：严格模式可能导致多次拒绝和更高的 Token 消耗。",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="审查标准",
                en_US="Review Standard",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="strict: 严格(代码+业务), standard: 标准(代码+核心业务), lenient: 宽松(仅代码)。注意：严格模式可能导致多次拒绝和更高的 Token 消耗。",
                en_US="strict: Strict(Code+Biz), standard: Standard(Code+Core Biz), lenient: Lenient(Code Only). strict mode may cost more tokens.",
            ),
        ).model_dump(),
    )

    # ==================== 并发控制 ====================

    MAX_CONCURRENT_AGENTS_PER_CHAT: int = Field(
        default=3,
        title="单会话最大并发 Agent 数",
        description="同一会话同时运行的最大 Agent 数量",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="单会话最大并发 Agent 数",
                en_US="Max Concurrent Agents Per Chat",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="同一会话同时运行的最大 Agent 数量",
                en_US="Maximum number of agents running concurrently in a single chat",
            ),
        ).model_dump(),
    )

    # ==================== 历史记录 ====================

    MAX_COMPLETED_HISTORY: int = Field(
        default=10,
        title="历史任务保留数",
        description="每个会话保留的已完成任务数量",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="历史任务保留数",
                en_US="Max Completed History",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="每个会话保留的已完成任务数量",
                en_US="Number of completed tasks retained per chat",
            ),
        ).model_dump(),
    )

    # ==================== 迭代控制 ====================

    MAX_ITERATIONS: int = Field(
        default=10,
        title="最大迭代次数",
        description="单个 Agent 最大修改迭代次数",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="最大迭代次数",
                en_US="Max Iterations",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="单个 Agent 最大修改迭代次数",
                en_US="Maximum modification iterations per agent",
            ),
        ).model_dump(),
    )

    MAX_REVIEW_ROUNDS: int = Field(
        default=3,
        title="最大审查重试轮次",
        description="达到此轮次后若仍未通过，自动放行但标记需人工介入",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="最大审查重试轮次",
                en_US="Max Review Rounds",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="达到此轮次后若仍未通过，自动放行但标记需人工介入",
                en_US="Auto-pass with manual intervention flag if still failing after this many rounds",
            ),
        ).model_dump(),
    )

    # ==================== 超时控制 ====================

    AGENT_TIMEOUT_MINUTES: int = Field(
        default=10,
        title="Agent 超时时间（分钟）",
        description="Agent 无响应超时时间，超时后自动标记为失败",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="Agent 超时时间（分钟）",
                en_US="Agent Timeout (Minutes)",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="Agent 无响应超时时间，超时后自动标记为失败",
                en_US="Timeout in minutes for agent unresponsiveness, automatically marked as failed",
            ),
        ).model_dump(),
    )

    AUTO_ARCHIVE_MINUTES: int = Field(
        default=60,
        title="自动归档时间（分钟）",
        description="已完成的 Agent 在无访问后自动归档的时间",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="自动归档时间（分钟）",
                en_US="Auto Archive Time (Minutes)",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="已完成的 Agent 在无访问后自动归档的时间",
                en_US="Time in minutes before completed agents are automatically archived after no access",
            ),
        ).model_dump(),
    )

    # ==================== Agent 层级控制 ====================

    MAX_AGENT_DEPTH: int = Field(
        default=5,
        title="最大 Agent 嵌套深度",
        description="子 Agent 可创建子 Agent 的最大嵌套层数，达到后禁止继续创建",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="最大 Agent 嵌套深度",
                en_US="Max Agent Depth",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="子 Agent 可创建子 Agent 的最大嵌套层数，达到后禁止继续创建",
                en_US="Maximum nesting depth for sub-agents, prevents further creation when reached",
            ),
        ).model_dump(),
    )

    # ==================== 身份呈现配置 ====================

    LANGUAGE: str = Field(
        default="zh-cn",
        title="用户语言",
        description="生成的网页内容主要用户语言 (如 zh-cn, en-us, ja-jp)",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="用户语言",
                en_US="User Language",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="生成的网页内容主要用户语言 (如 zh-cn, en-us, ja-jp)",
                en_US="Primary user language for generated web content (e.g. zh-cn, en-us, ja-jp)",
            ),
        ).model_dump(),
    )

    TRANSPARENT_SUB_AGENT: bool = Field(
        default=True,
        title="透明展示子Agent工作",
        description="开启后，主Agent向用户表达时会明确提及助手；关闭后，主Agent会将子Agent的工作视为自己的工作",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="透明展示子Agent工作",
                en_US="Transparent Sub-Agent Work",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="开启后，主Agent向用户表达时会明确提及助手；关闭后，主Agent会将子Agent的工作视为自己的工作",
                en_US="If enabled, main agent explicitly mentions assistants; if disabled, sub-agent work is presented as its own",
            ),
        ).model_dump(),
    )


# 获取配置实例
config: WebAppConfig = plugin.get_config(WebAppConfig)

# 获取插件存储
store = plugin.store
