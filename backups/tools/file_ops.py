"""
File Operations Tool - Read, write, and list files in a sandboxed directory.
文件操作工具 —— 在沙箱目录内读取、写入和列出文件。

Provides basic file I/O capabilities within a configured sandbox directory,
preventing access to files outside the sandbox for safety.
在配置的沙箱目录内提供基础文件 I/O 能力，
通过路径校验防止越权访问沙箱外的文件（路径穿越攻击防护）。
"""

from __future__ import annotations

import logging
import os
from typing import Any

import config
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class FileOpsTool(BaseTool):
    """
    File operations restricted to a sandbox directory.
    文件操作工具，所有操作限制在沙箱目录内。
    """

    def __init__(self):
        self._sandbox = config.SANDBOX_DIR  # 沙箱根目录
        os.makedirs(self._sandbox, exist_ok=True)  # 确保沙箱目录存在

    @property
    def name(self) -> str:
        return "file_ops"

    @property
    def description(self) -> str:
        return (
            "Perform file operations: read, write, or list files. "
            "All operations are restricted to the sandbox directory. "
            "Use action='read' with filename to read, action='write' with "
            "filename and content to write, action='list' to list files."
            # 执行文件操作：读取、写入或列出文件。
            # 所有操作限制在沙箱目录内。
            # action='read' + filename 读取文件；
            # action='write' + filename + content 写入文件；
            # action='list' 列出沙箱内所有文件。
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write", "list"],
                    "description": "The file operation to perform",  # 要执行的文件操作类型
                },
                "filename": {
                    "type": "string",
                    "description": "Name of the file (for read/write operations)",  # 文件名（读/写操作必填）
                },
                "content": {
                    "type": "string",
                    "description": "Content to write (for write operation)",  # 要写入的内容（写操作专用）
                },
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs: Any) -> str:
        action = kwargs.get("action", "")
        filename = kwargs.get("filename", "")
        content = kwargs.get("content", "")

        if action == "list":
            return self._list_files()
        elif action == "read":
            return self._read_file(filename)
        elif action == "write":
            return self._write_file(filename, content)
        else:
            return f"Error: Unknown action '{action}'. Use 'read', 'write', or 'list'."

    def _safe_path(self, filename: str) -> str | None:
        """
        Resolve filename within sandbox; return None if it escapes.
        在沙箱内解析文件名的绝对路径；若路径逃出沙箱则返回 None。
        通过 os.path.realpath 解析符号链接和 ../.. 等相对路径，防止路径穿越攻击。
        """
        path = os.path.realpath(os.path.join(self._sandbox, filename))
        if not path.startswith(os.path.realpath(self._sandbox)):
            return None  # 路径逃出沙箱，拒绝访问
        return path

    def _list_files(self) -> str:
        """列出沙箱目录中的所有文件。"""
        try:
            files = os.listdir(self._sandbox)
            if not files:
                return f"Sandbox directory is empty: {self._sandbox}"
            return f"Files in sandbox:\n" + "\n".join(f"  - {f}" for f in sorted(files))
        except Exception as exc:
            return f"Error listing files: {exc}"

    def _read_file(self, filename: str) -> str:
        """读取沙箱内指定文件的内容。"""
        if not filename:
            return "Error: filename is required for read operation."
        path = self._safe_path(filename)
        if path is None:
            return "Error: Access denied - path outside sandbox."
        if not os.path.exists(path):
            return f"Error: File not found: {filename}"
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f"Content of {filename}:\n{f.read()}"
        except Exception as exc:
            return f"Error reading file: {exc}"

    def _write_file(self, filename: str, content: str) -> str:
        """将内容写入沙箱内的指定文件（不存在则创建，存在则覆盖）。"""
        if not filename:
            return "Error: filename is required for write operation."
        path = self._safe_path(filename)
        if path is None:
            return "Error: Access denied - path outside sandbox."
        try:
            # 若文件路径包含子目录，自动创建
            os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) != self._sandbox else None
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully wrote {len(content)} characters to {filename}"
        except Exception as exc:
            return f"Error writing file: {exc}"
