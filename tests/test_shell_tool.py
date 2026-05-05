"""
Tests for ShellTool.
Shell 工具测试。
"""

import asyncio
import pytest

from tools.shell_tool import ShellTool


class TestShellToolProperties:
    """Test ShellTool basic properties."""

    def test_name(self):
        tool = ShellTool()
        assert tool.name == "execute_shell"

    def test_description(self):
        tool = ShellTool()
        assert "shell command" in tool.description.lower()

    def test_parameters_schema(self):
        tool = ShellTool()
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        assert "command" in schema["properties"]
        assert "timeout" in schema["properties"]
        assert schema["required"] == ["command"]

    def test_to_openai_tool(self):
        tool = ShellTool()
        ot = tool.to_openai_tool()
        assert ot["type"] == "function"
        assert ot["function"]["name"] == "execute_shell"


class TestShellToolExecution:
    """Test ShellTool command execution."""

    @pytest.mark.asyncio
    async def test_basic_command(self):
        tool = ShellTool()
        result = await tool.execute(command="echo hello")
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_command_with_pipe(self):
        tool = ShellTool()
        result = await tool.execute(command="echo 'abc def' | wc -w")
        assert "2" in result

    @pytest.mark.asyncio
    async def test_empty_command(self):
        tool = ShellTool()
        result = await tool.execute(command="")
        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_no_output_command(self):
        tool = ShellTool()
        result = await tool.execute(command="true")
        assert "successfully" in result.lower()

    @pytest.mark.asyncio
    async def test_command_with_stderr(self):
        tool = ShellTool()
        result = await tool.execute(command="echo err >&2")
        assert "err" in result

    @pytest.mark.asyncio
    async def test_nonzero_exit_code(self):
        tool = ShellTool()
        result = await tool.execute(command="exit 42")
        assert "42" in result


class TestShellToolBlacklist:
    """Test command blacklist safety checks."""

    def test_rm_rf_root_blocked(self):
        tool = ShellTool()
        assert tool._check_blocked("rm -rf /") is not None

    def test_mkfs_blocked(self):
        tool = ShellTool()
        assert tool._check_blocked("mkfs.ext4 /dev/sda1") is not None

    def test_dd_blocked(self):
        tool = ShellTool()
        assert tool._check_blocked("dd if=/dev/zero of=/dev/sda") is not None

    def test_sudo_rm_rf_blocked(self):
        tool = ShellTool()
        assert tool._check_blocked("sudo rm -rf /home") is not None

    def test_safe_command_passes(self):
        tool = ShellTool()
        assert tool._check_blocked("ls -la") is None
        assert tool._check_blocked("echo hello") is None
        assert tool._check_blocked("grep pattern file.txt") is None

    def test_blocked_command_returns_error(self):
        tool = ShellTool()
        result = await_sync(tool.execute(command="rm -rf /"))
        assert result.startswith("Error:")
        assert "blocked" in result.lower()


class TestShellToolTimeout:
    """Test timeout behavior."""

    @pytest.mark.asyncio
    async def test_custom_timeout(self):
        tool = ShellTool()
        result = await tool.execute(command="echo fast", timeout=5)
        assert "fast" in result

    @pytest.mark.asyncio
    async def test_timeout_exceeded(self):
        tool = ShellTool()
        result = await tool.execute(command="sleep 60", timeout=1)
        assert "timed out" in result.lower()


def await_sync(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
