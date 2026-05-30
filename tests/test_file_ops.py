"""
P1-5 regression: FileOpsTool sandbox path validation must use an os.sep boundary
so absolute-path / parent-traversal / sibling-prefix escapes are all denied.
"""

import os
from unittest.mock import patch

import pytest

import config
from tools.file_ops import FileOpsTool


@pytest.fixture
def sandbox_tool(tmp_path):
    sandbox = tmp_path / "sandbox"
    with patch.object(config, "SANDBOX_DIR", str(sandbox)):
        yield FileOpsTool(), tmp_path, sandbox


class TestFileOpsSandbox:
    @pytest.mark.asyncio
    async def test_write_and_read_within_sandbox(self, sandbox_tool):
        tool, _tmp, _sandbox = sandbox_tool
        w = await tool.execute(action="write", filename="note.txt", content="hi")
        assert not w.startswith("Error:")
        r = await tool.execute(action="read", filename="note.txt")
        assert "hi" in r

    @pytest.mark.asyncio
    async def test_parent_traversal_denied(self, sandbox_tool):
        tool, _tmp, _sandbox = sandbox_tool
        r = await tool.execute(action="read", filename="../../etc/passwd")
        assert "Access denied" in r

    @pytest.mark.asyncio
    async def test_absolute_path_outside_denied(self, sandbox_tool):
        tool, _tmp, _sandbox = sandbox_tool
        r = await tool.execute(action="read", filename="/etc/passwd")
        assert "Access denied" in r

    @pytest.mark.asyncio
    async def test_sibling_prefix_escape_denied(self, sandbox_tool):
        # The core bug: a sibling dir sharing the sandbox name PREFIX
        # (<sandbox>_escape) must NOT be treated as inside the sandbox.
        tool, tmp_path, sandbox = sandbox_tool
        sibling = tmp_path / "sandbox_escape"
        sibling.mkdir()
        (sibling / "secret.txt").write_text("leaked")

        r = await tool.execute(action="read", filename="../sandbox_escape/secret.txt")
        assert "Access denied" in r

    @pytest.mark.asyncio
    async def test_sibling_prefix_write_denied(self, sandbox_tool):
        tool, tmp_path, sandbox = sandbox_tool
        w = await tool.execute(
            action="write", filename="../sandbox_escape/x.txt", content="pwn",
        )
        assert "Access denied" in w
        assert not (tmp_path / "sandbox_escape" / "x.txt").exists()
