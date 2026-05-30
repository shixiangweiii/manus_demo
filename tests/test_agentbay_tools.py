from __future__ import annotations

from types import SimpleNamespace
import pytest

import config
from guardrails.engine import GuardrailEngine
from guardrails.models import GuardrailAction
from tools.agentbay.browser_tool import AgentBayBrowserTool, validate_public_url
from tools.agentbay.code_tool import AgentBayCodeTool


class _Handle:
    def __init__(self, session_id: str = "s-test"):
        self.session_id = session_id
        self.session = SimpleNamespace()
        self.agent_bay = SimpleNamespace()


@pytest.mark.asyncio
async def test_agentbay_code_success_deletes_session(monkeypatch):
    tool = AgentBayCodeTool()
    handle = _Handle()
    calls = []

    async def fake_create(image_id, tool_name):
        calls.append(("create", image_id, tool_name))
        return handle

    async def fake_delete(h):
        calls.append(("delete", h.session_id))
        return True, ""

    async def fake_run(session, code, language, timeout_s):
        calls.append(("run", code, language, timeout_s))
        logs = SimpleNamespace(stdout=["hello\n"], stderr=[])
        return SimpleNamespace(success=True, logs=logs, results=[], result="", request_id="req-1")

    monkeypatch.setattr("tools.agentbay.code_tool.create_session", fake_create)
    monkeypatch.setattr("tools.agentbay.code_tool.delete_session", fake_delete)
    monkeypatch.setattr(AgentBayCodeTool, "_run_code", staticmethod(fake_run))
    monkeypatch.setattr(config, "AGENTBAY_CODE_IMAGE", "code_latest")

    result = await tool.execute(code="print('hello')", timeout_s=999)

    assert "AgentBay CodeSpace execution succeeded" in result
    assert "hello" in result
    assert "session_deleted: true" in result
    assert calls == [
        ("create", "code_latest", "agentbay_code"),
        ("run", "print('hello')", "python", 60),
        ("delete", "s-test"),
    ]


@pytest.mark.asyncio
async def test_agentbay_code_error_still_deletes_session(monkeypatch):
    tool = AgentBayCodeTool()
    handle = _Handle("s-fail")
    deleted = False

    async def fake_create(image_id, tool_name):
        return handle

    async def fake_delete(h):
        nonlocal deleted
        deleted = True
        return True, ""

    async def fake_run(session, code, language, timeout_s):
        raise RuntimeError("remote boom")

    monkeypatch.setattr("tools.agentbay.code_tool.create_session", fake_create)
    monkeypatch.setattr("tools.agentbay.code_tool.delete_session", fake_delete)
    monkeypatch.setattr(AgentBayCodeTool, "_run_code", staticmethod(fake_run))

    result = await tool.execute(code="print(1)")

    assert result.startswith("Error:")
    assert "s-fail" in result
    assert deleted is True


@pytest.mark.asyncio
async def test_agentbay_browser_success_deletes_session(monkeypatch):
    tool = AgentBayBrowserTool()
    handle = _Handle("s-browser")
    deleted = False

    async def fake_create(image_id, tool_name):
        assert image_id == "browser_latest"
        assert tool_name == "agentbay_browser"
        return handle

    async def fake_delete(h):
        nonlocal deleted
        deleted = True
        return True, ""

    def fake_run(session, session_id, url, operation, selector, full_page, timeout_ms, max_chars):
        assert session_id == "s-browser"
        assert url == "https://www.aliyun.com"
        assert operation == "title"
        return "AgentBay BrowserUse operation succeeded.\ntitle: ok"

    monkeypatch.setattr("tools.agentbay.browser_tool.create_session", fake_create)
    monkeypatch.setattr("tools.agentbay.browser_tool.delete_session", fake_delete)
    monkeypatch.setattr(AgentBayBrowserTool, "_run_browser_sync", staticmethod(fake_run))
    monkeypatch.setattr(config, "AGENTBAY_BROWSER_IMAGE", "browser_latest")

    result = await tool.execute(url="https://www.aliyun.com")

    assert "title: ok" in result
    assert "session_deleted: true" in result
    assert deleted is True


def test_agentbay_browser_url_validation_blocks_local_targets():
    assert validate_public_url("file:///etc/passwd")
    assert validate_public_url("http://localhost:8000")
    assert validate_public_url("http://127.0.0.1:8000")
    assert validate_public_url("http://10.0.0.1")
    assert validate_public_url("https://www.aliyun.com") is None


@pytest.mark.asyncio
async def test_guardrails_cover_agentbay_tools(monkeypatch):
    monkeypatch.setattr(config, "GUARDRAIL_TOOL_ENABLED", True)
    monkeypatch.setattr(config, "GUARDRAIL_TOOL_MODE", "block")
    engine = GuardrailEngine()

    code_decision = await engine.check_tool_input("agentbay_code", {"code": "import os\nos.system('whoami')"})
    assert code_decision.action == GuardrailAction.BLOCK

    browser_decision = await engine.check_tool_input("agentbay_browser", {"url": "http://127.0.0.1:8000"})
    assert browser_decision.action == GuardrailAction.BLOCK

    ok_decision = await engine.check_tool_input("agentbay_browser", {"url": "https://www.aliyun.com"})
    assert ok_decision.action == GuardrailAction.ALLOW


@pytest.mark.asyncio
async def test_main_build_tools_registers_agentbay_when_enabled(monkeypatch):
    import main

    monkeypatch.setattr(config, "AGENTBAY_ENABLED", True)
    monkeypatch.setattr(config, "AGENTBAY_API_KEY", "ak-test")
    monkeypatch.setattr(config, "AGENTBAY_CODE_TOOL_ENABLED", True)
    monkeypatch.setattr(config, "AGENTBAY_BROWSER_TOOL_ENABLED", True)
    monkeypatch.setattr(config, "MCP_BRIDGE_ENABLED", False)

    tools = await main._build_tools()
    names = {t.name for t in tools}

    assert "agentbay_code" in names
    assert "agentbay_browser" in names


@pytest.mark.asyncio
async def test_main_build_tools_skips_agentbay_without_key(monkeypatch):
    import main

    monkeypatch.setattr(config, "AGENTBAY_ENABLED", True)
    monkeypatch.setattr(config, "AGENTBAY_API_KEY", "")
    monkeypatch.setattr(config, "MCP_BRIDGE_ENABLED", False)

    tools = await main._build_tools()
    names = {t.name for t in tools}

    assert "agentbay_code" not in names
    assert "agentbay_browser" not in names
