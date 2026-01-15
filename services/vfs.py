"""Virtual File System (VFS)

Manages the in-memory state of the project files for the WebDev plugin.
This acts as reliability layer between the Agents and the Compiler.
"""

from typing import Callable, Dict, List, Optional

from nekro_agent.api.core import logger


class WriteResult:
    """写入操作结果"""

    def __init__(self, success: bool, error: Optional[str] = None):
        self.success = success
        self.error = error


class ProjectContext:
    """Represents the state of a user's project"""

    def __init__(self, chat_key: str):
        self.chat_key = chat_key
        # filepath (relative to src) -> content
        self.files: Dict[str, str] = {}
        # filepath -> owner_agent_id (文件所有权)
        self.file_owners: Dict[str, str] = {}

    def write_file(
        self,
        path: str,
        content: str,
        agent_id: Optional[str] = None,
        force: bool = False,
        parent_id_checker: Optional[Callable[[str, str], bool]] = None,
        owner_status_checker: Optional[Callable[[str], str]] = None,
    ) -> WriteResult:
        """Write content to a file with hierarchical permission check

        Args:
            path: 文件路径
            content: 文件内容
            agent_id: 写入者的 Agent ID
            force: 是否强制写入（跳过所有权检查）
            parent_id_checker: 回调函数，检查 writer 是否为 owner 的父 Agent
                              签名: (writer_id: str, owner_id: str) -> bool
            owner_status_checker: 回调函数，检查 owner 的状态
                                 签名: (agent_id: str) -> str (返回状态如 'WORKING', 'COMPLETED')

        Returns:
            WriteResult: 写入结果，失败时包含错误信息
        """
        clean_path = path.strip().lstrip("./").lstrip("/")

        # 所有权检查
        current_owner = self.file_owners.get(clean_path)

        if current_owner and agent_id and current_owner != agent_id:
            # 场景 1: 父 Agent 覆盖子 Agent 的文件
            if parent_id_checker and parent_id_checker(agent_id, current_owner):
                logger.info(
                    f"[VFS] 👨‍👦 Parent Override: {agent_id} 覆盖了子 Agent {current_owner} 的文件 {clean_path}",
                )
                self.file_owners[clean_path] = agent_id

            # 场景 2: 智能转让（owner 已完成）
            elif owner_status_checker:
                owner_status = owner_status_checker(current_owner)
                if owner_status in ("completed", "failed", "cancelled"):
                    logger.info(
                        f"[VFS] 🔄 Smart Transfer: {clean_path} 从 {current_owner}({owner_status}) "
                        f"自动转让给 {agent_id}",
                    )
                    self.file_owners[clean_path] = agent_id
                elif owner_status == "working":
                    error_msg = (
                        f"文件 {clean_path} 的所有者 {current_owner} 正在工作中（状态: {owner_status}）。\n"
                        f"你无法编辑此文件。请等待其完成或联系你的父 Agent 处理。"
                    )
                    logger.warning(
                        f"[VFS] 🚫 所有权冲突: {agent_id} 尝试写入 {clean_path}，"
                        f"但 owner {current_owner} 正在 WORKING",
                    )
                    return WriteResult(success=False, error=error_msg)
                else:
                    error_msg = (
                        f"文件 {clean_path} 的所有者 {current_owner} 处于 {owner_status} 状态。\n"
                        f"无法确定是否可以安全转让所有权。"
                    )
                    logger.warning(
                        f"[VFS] ⚠️ 不确定状态: {agent_id} 尝试写入 {clean_path}，"
                        f"owner {current_owner} 状态为 {owner_status}",
                    )
                    return WriteResult(success=False, error=error_msg)

            # 场景 3: 强制写入
            elif force:
                logger.warning(
                    f"[VFS] ⚡ Force Write: {agent_id} 强制写入 {clean_path}（原 owner: {current_owner}）",
                )
                self.file_owners[clean_path] = agent_id

            # 场景 4: 拒绝写入
            else:
                error_msg = (
                    f"文件 {clean_path} 的所有权已被转让给 {current_owner}。\n"
                    f"你无法再编辑此文件。请联系上级 Agent 了解情况。"
                )
                logger.warning(
                    f"[VFS] 🚫 所有权冲突: {agent_id} 尝试写入 {clean_path}，但 owner 是 {current_owner}",
                )
                return WriteResult(success=False, error=error_msg)

        # 执行写入
        self.files[clean_path] = content

        # 如果文件没有 owner，且有 agent_id，则设置 owner
        if not current_owner and agent_id:
            self.file_owners[clean_path] = agent_id
            logger.info(f"[VFS] 🔑 设置文件所有权: {clean_path} -> {agent_id}")

        logger.info(f"[VFS] 💾 Wrote file: {clean_path} ({len(content)} chars)")
        return WriteResult(success=True)

    def transfer_ownership(
        self,
        path: str,
        new_owner: str,
        force: bool = False,
    ) -> bool:
        """转让文件所有权

        Args:
            path: 文件路径
            new_owner: 新 owner 的 Agent ID
            force: 强制转让（无视当前使用状态）

        Returns:
            是否成功转让
        """
        clean_path = path.strip().lstrip("./").lstrip("/")
        old_owner = self.file_owners.get(clean_path)
        self.file_owners[clean_path] = new_owner
        logger.info(
            f"[VFS] 🔄 所有权转让: {clean_path}: {old_owner or 'None'} -> {new_owner}"
            + (" [FORCED]" if force else ""),
        )
        return True

    def delete_file(
        self,
        path: str,
        confirmed: bool = False,
        working_agents: Optional[List[str]] = None,
    ) -> WriteResult:
        """删除文件

        Args:
            path: 文件路径
            agent_id: 删除者的 Agent ID
            confirmed: 是否已确认删除（强制删除，即使文件正在被使用）
            working_agents: 当前 WORKING 状态的 Agent ID 列表（由调用者传入）

        Returns:
            WriteResult: 删除结果
        """
        clean_path = path.strip().lstrip("./").lstrip("/")

        if clean_path not in self.files:
            return WriteResult(success=False, error=f"文件 {clean_path} 不存在")

        # 检查文件 owner 是否在 WORKING 状态
        owner = self.file_owners.get(clean_path)
        if owner and working_agents and owner in working_agents and not confirmed:
            error_msg = (
                f"文件 {clean_path} 的所有者 {owner} 正在工作中，无法删除。"
                f'若仍需删除，请使用 confirmed="true" 强制删除。'
            )
            logger.warning(
                f"[VFS] 🚫 删除被拒绝: {clean_path} 的 owner {owner} 正在 WORKING",
            )
            return WriteResult(success=False, error=error_msg)

        del self.files[clean_path]
        if clean_path in self.file_owners:
            del self.file_owners[clean_path]

        logger.info(f"[VFS] 🗑️ Deleted file: {clean_path}")
        return WriteResult(success=True)

    def get_owner(self, path: str) -> Optional[str]:
        """获取文件所有者"""
        clean_path = path.strip().lstrip("./").lstrip("/")
        return self.file_owners.get(clean_path)

    def read_file(self, path: str) -> Optional[str]:
        """Read content from a file"""
        clean_path = path.strip().lstrip("./").lstrip("/")
        return self.files.get(clean_path)

    def extract_exports(self, path: str) -> List[str]:
        """从 TypeScript/JavaScript 文件中提取导出名

        支持：
        - export const/let/var/function/class NAME
        - export default function/class NAME
        - export default NAME (匿名则返回 'default')
        - export { A, B, C }
        - export type/interface NAME

        Returns:
            导出名列表，默认导出用 'default' 表示
        """
        import re

        content = self.read_file(path)
        if not content:
            return []

        exports: List[str] = []

        # 1. export const/let/var/function/class NAME
        pattern1 = r"export\s+(?:const|let|var|function|class|async\s+function)\s+(\w+)"
        exports.extend(re.findall(pattern1, content))

        # 2. export type/interface NAME
        pattern2 = r"export\s+(?:type|interface)\s+(\w+)"
        exports.extend(re.findall(pattern2, content))

        # 3. export default function/class NAME 或匿名
        pattern3 = r"export\s+default\s+(?:function|class)\s+(\w+)?"
        for match in re.finditer(pattern3, content):
            name = match.group(1)
            if name:
                exports.append(f"default ({name})")
            elif "default" not in [e for e in exports if e.startswith("default")]:
                exports.append("default")

        # 4. export default NAME (变量)
        pattern4 = r"export\s+default\s+(\w+)\s*;"
        for match in re.finditer(pattern4, content):
            name = match.group(1)
            if (
                name not in ("function", "class", "async")
                and f"default ({name})" not in exports
                and "default" not in exports
            ):
                exports.append(f"default ({name})")

        # 5. export { A, B, C } 或 export { A as B }
        pattern5 = r"export\s*\{([^}]+)\}"
        for match in re.finditer(pattern5, content):
            items = match.group(1)
            for item in items.split(","):
                item = item.strip()
                if " as " in item:
                    # export { foo as bar } => bar 是导出名
                    parts = item.split(" as ")
                    if len(parts) == 2:
                        exports.append(parts[1].strip())
                else:
                    exports.append(item)

        # 去重
        return list(dict.fromkeys(exports))

    def list_files(self) -> List[str]:
        """List all files in the project"""
        return list(self.files.keys())

    def get_snapshot(self) -> Dict[str, str]:
        """Get a snapshot of all files for compilation"""
        return self.files.copy()

    def clear(self) -> None:
        """Clear all files"""
        self.files.clear()
        self.file_owners.clear()


# Global VFS Manager (chat_key -> ProjectContext)
_contexts: Dict[str, ProjectContext] = {}


def get_project_context(chat_key: str) -> ProjectContext:
    """Get or create a project context for a chat"""
    if chat_key not in _contexts:
        _contexts[chat_key] = ProjectContext(chat_key)
    return _contexts[chat_key]
