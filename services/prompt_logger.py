"""提示词日志保存工具"""

import json
from datetime import datetime
from pathlib import Path
from typing import List

from nekro_agent.services.agent.creator import OpenAIChatMessage


def save_prompt_log_to_file(
    agent_id: str,
    messages: List[OpenAIChatMessage],
    plugin_data_dir: str,
) -> str:
    """保存提示词日志到文件
    
    Args:
        agent_id: Agent ID
        messages: 消息列表
        plugin_data_dir: 插件数据目录
        
    Returns:
        保存的日志文件路径
    """
    # 创建 prompts 目录
    prompts_dir = Path(plugin_data_dir) / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成文件名：YYYYMMDD_HHMMSS_AgentID.log
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{agent_id}.log"
    log_path = prompts_dir / filename
    
    # 构建日志内容
    log_content = f"""{'=' * 80}
提示词日志 - {agent_id}
{'=' * 80}
时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Agent ID: {agent_id}
消息数量: {len(messages)}
{'=' * 80}

"""
    
    for i, msg in enumerate(messages, 1):
        log_content += f"[{i}] Role: {msg.role}\n"
        log_content += f"Content:\n{msg.content}\n"
        log_content += f"{'-' * 80}\n\n"
    
    # 写入文件
    log_path.write_text(log_content, encoding="utf-8")
    
    return str(log_path)
