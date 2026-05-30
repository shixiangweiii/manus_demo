"""
Mock MCP Server for testing — provides echo, add, fail, and complex_schema tools.

测试用 Mock MCP 服务器 —— 提供 echo、add、fail、complex_schema 工具。
可通过 stdio 或 streamable_http 方式启动。
"""

from mcp.server.fastmcp import FastMCP

mock_server = FastMCP("test-mock")


@mock_server.tool()
def echo(text: str) -> str:
    """Echo the input text."""
    return text


@mock_server.tool()
def add(a: int, b: int) -> str:
    """Add two numbers and return the result."""
    return str(a + b)


@mock_server.tool()
def fail(message: str) -> str:
    """Always raises an error."""
    raise RuntimeError(f"Intentional failure: {message}")


@mock_server.tool()
def greet(name: str, greeting: str = "Hello") -> str:
    """Generate a greeting message."""
    return f"{greeting}, {name}!"


if __name__ == "__main__":
    import sys
    if "--stdio" in sys.argv:
        mock_server.run(transport="stdio")
    else:
        mock_server.run(transport="streamable-http", host="127.0.0.1", port=9876)
