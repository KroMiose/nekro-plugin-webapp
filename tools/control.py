"""控制工具

提供任务完成标记等控制功能。
"""

from ..core.context import AgentExecutionState, ToolContext
from ..services.compiler_client import compile_project
from . import agent_tool
from .compile import enhance_compile_error


@agent_tool(
    name="done",
    description="任务完成提交。会自动执行编译验证，只有编译通过才会真正提交。",
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "完成总结，描述实现了什么功能",
            },
            "title": {
                "type": "string",
                "description": "WebApp 页面标题（用于浏览器显示）",
            },
        },
        "required": ["summary"],
    },
)
async def done(ctx: ToolContext, summary: str, title: str = "") -> str:
    """标记完成（自动编译）"""
    files = ctx.project.get_snapshot()

    # 1. 基础检查
    if not files:
        return "❌ 项目为空，无法提交"

    # 2. 执行编译
    if ctx.tracer:
        ctx.tracer.log_event(
            event_type=ctx.tracer.EVENT.COMPILE_START,
            agent_id=ctx.task_id,
            message="提交前自动编译",
            file_count=len(files),
        )

    success, output, externals = await compile_project(
        files=files,
        tracer=ctx.tracer,
        agent_id=ctx.task_id,
    )

    ctx.state.compile_success = success

    # 3. 编译失败处理
    if not success:
        if ctx.tracer:
            ctx.tracer.log_event(
                event_type=ctx.tracer.EVENT.COMPILE_FAILED,
                agent_id=ctx.task_id,
                message="提交拒绝：编译失败",
                error=output[:500],
            )
        
        enhanced_error = enhance_compile_error(output, ctx)
        ctx.state.last_error = enhanced_error
        # 注意：这里不设置 completed=True
        return f"❌ 提交被拒绝（编译失败）:\n{enhanced_error}"

    # 4. 提交成功
    # 4. 提交成功
    ctx.state.completed = True
    
    # 更新运行时标题
    if title:
        from ..services.runtime_state import runtime_state
        state = runtime_state.get_state(ctx.chat_key, ctx.task_id)
        if state:
            state.title = title
    
    if ctx.tracer:
        ctx.tracer.log_event(
            event_type=ctx.tracer.EVENT.TASK_DONE,
            agent_id=ctx.task_id,
            message="任务完成（编译通过）",
            summary=summary,
            output_size=len(output),
        )
    
    return f"✅ 任务成功提交!\nWebTitle: {title or 'Default'}\n编译输出: {output[:200]}...\n总结: {summary}"


@agent_tool(
    name="abort",
    description="放弃任务。仅在遇到无法解决的问题时使用。",
    parameters={
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "放弃原因",
            },
        },
        "required": ["reason"],
    },
)
async def abort(ctx: ToolContext, reason: str) -> str:
    """放弃任务"""
    ctx.state.completed = True
    ctx.state.last_error = reason
    ctx.state.execution_state = AgentExecutionState.FAILED
    
    if ctx.tracer:
        ctx.tracer.log_event(
            event_type=ctx.tracer.EVENT.TASK_ABORT,
            agent_id=ctx.task_id,
            message="任务放弃",
            reason=reason,
            level="WARNING",
        )
    
    return f"⚠️ 任务放弃: {reason}"
