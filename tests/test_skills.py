"""
Unit tests for the v20 Agent Skills module.
v20 智能体技能模块的单元测试。

Tests cover:
- skills/models.py: SkillMeta, SkillDef dataclass construction and validation
- skills/loader.py: SkillLoader frontmatter parsing, name validation, directory scanning
- skills/registry.py: SkillRegistry register/get/format_descriptions/load_full_content
- skills/activation.py: SkillActivationTool execute/reset_task_state
- agents/prompt_utils.py: skill guidance injection
"""

import asyncio
import os
import tempfile
import shutil

import pytest

import config
from skills.models import SkillMeta, SkillDef, SKILL_NAME_PATTERN, RESERVED_SKILL_PREFIXES
from skills.loader import SkillLoader
from skills.registry import SkillRegistry
from skills.activation import SkillActivationTool


# ======================================================================
# Fixtures for global state isolation
# 全局状态隔离的 fixtures
# ======================================================================

@pytest.fixture(autouse=True)
def _reset_prompt_utils_global():
    """Reset module-level globals in prompt_utils between tests to prevent state leak.
    在测试之间重置 prompt_utils 的模块级全局变量，防止状态泄漏。
    """
    import agents.prompt_utils as pu
    original_desc = pu._SKILL_DESCRIPTIONS
    original_hitl = pu._HITL_RUNTIME_OVERRIDE
    yield
    pu._SKILL_DESCRIPTIONS = original_desc
    pu._HITL_RUNTIME_OVERRIDE = original_hitl


# ======================================================================
# SkillMeta / SkillDef dataclass tests
# ======================================================================

class TestSkillMeta:
    def test_basic_construction(self):
        meta = SkillMeta(name="test-skill", description="A test skill")
        assert meta.name == "test-skill"
        assert meta.description == "A test skill"
        assert meta.license == ""  # Auto-detected by loader from directory
        assert meta.compatibility == ">=20.0"
        assert meta.metadata == {}
        assert meta.allowed_tools == []

    def test_full_construction(self):
        meta = SkillMeta(
            name="web-research",
            description="Automated web research workflow",
            license="user",
            compatibility=">=20.0",
            metadata={"author": "test", "version": "1.0"},
            allowed_tools=["web_search", "fetch_url"],
        )
        assert meta.license == "user"
        assert meta.allowed_tools == ["web_search", "fetch_url"]
        assert meta.metadata["author"] == "test"

    def test_name_pattern_valid(self):
        assert SKILL_NAME_PATTERN.match("hello-world")
        assert SKILL_NAME_PATTERN.match("python-code-review")
        assert SKILL_NAME_PATTERN.match("skill123")
        assert SKILL_NAME_PATTERN.match("a")

    def test_name_pattern_invalid(self):
        assert not SKILL_NAME_PATTERN.match("Hello-World")  # uppercase
        assert not SKILL_NAME_PATTERN.match("hello world")   # space
        assert not SKILL_NAME_PATTERN.match("hello_world")   # underscore
        assert not SKILL_NAME_PATTERN.match("")               # empty
        assert not SKILL_NAME_PATTERN.match("-hello")         # starts with hyphen


class TestSkillDef:
    def test_basic_construction(self):
        meta = SkillMeta(name="test", description="Test")
        sdef = SkillDef(meta=meta, skill_dir="/tmp/test", full_content="Hello")
        assert sdef.meta.name == "test"
        assert sdef.full_content == "Hello"
        assert sdef.scripts == []
        assert sdef.references == []
        assert sdef.assets == []


# ======================================================================
# SkillLoader tests
# ======================================================================

class TestSkillLoaderParseFrontmatter:
    def test_basic_frontmatter(self):
        content = "---\nname: test-skill\ndescription: A test\n---\n\nBody here"
        result, body_start = SkillLoader._parse_frontmatter(content)
        assert result["name"] == "test-skill"
        assert result["description"] == "A test"
        assert body_start > 0  # Body starts after frontmatter

    def test_frontmatter_with_allowed_tools_json(self):
        content = '---\nname: test\ndescription: desc\nallowed_tools: ["web_search", "fetch_url"]\n---\n\nBody'
        result, _ = SkillLoader._parse_frontmatter(content)
        assert result["allowed_tools"] == ["web_search", "fetch_url"]

    def test_frontmatter_with_allowed_tools_comma(self):
        content = '---\nname: test\ndescription: desc\nallowed_tools: web_search, fetch_url\n---\n\nBody'
        result, _ = SkillLoader._parse_frontmatter(content)
        assert result["allowed_tools"] == ["web_search", "fetch_url"]

    def test_frontmatter_with_allowed_tools_space_separated(self):
        """Spec format: space-separated tools like 'Bash(git:*) Read'."""
        content = '---\nname: test\ndescription: desc\nallowed_tools: web_search fetch_url\n---\n\nBody'
        result, _ = SkillLoader._parse_frontmatter(content)
        assert result["allowed_tools"] == ["web_search", "fetch_url"]

    def test_frontmatter_hyphenated_key_normalization(self):
        """Spec uses 'allowed-tools' (hyphen), loader normalizes to 'allowed_tools'."""
        content = '---\nname: test\ndescription: desc\nallowed-tools: web_search fetch_url\n---\n\nBody'
        result, _ = SkillLoader._parse_frontmatter(content)
        assert result["allowed_tools"] == ["web_search", "fetch_url"]
        assert "allowed-tools" not in result  # Hyphenated key should not exist

    def test_no_frontmatter(self):
        content = "Just some markdown content"
        result, body_start = SkillLoader._parse_frontmatter(content)
        assert result == {}
        assert body_start == 0

    def test_empty_frontmatter(self):
        content = "---\n---\n\nBody"
        result, _ = SkillLoader._parse_frontmatter(content)
        assert result == {}

    def test_frontmatter_with_comments(self):
        content = "---\n# This is a comment\nname: test\ndescription: desc\n---\n\nBody"
        result, _ = SkillLoader._parse_frontmatter(content)
        assert result["name"] == "test"
        assert result["description"] == "desc"

    def test_frontmatter_with_license_and_compatibility(self):
        content = "---\nname: test\ndescription: desc\nlicense: user\ncompatibility: \">=19.0\"\n---\n\nBody"
        result, _ = SkillLoader._parse_frontmatter(content)
        assert result["license"] == "user"
        assert ">=19.0" in result["compatibility"]


class TestSkillLoaderValidateName:
    def test_valid_name(self):
        assert SkillLoader._validate_name("hello-world", "hello-world") is None

    def test_empty_name(self):
        assert SkillLoader._validate_name("", "test") is not None

    def test_uppercase_name(self):
        assert SkillLoader._validate_name("Hello-World", "Hello-World") is not None

    def test_name_dirname_mismatch(self):
        error = SkillLoader._validate_name("foo", "bar")
        assert error is not None
        assert "does not match directory name" in error

    def test_reserved_prefix(self):
        for prefix in RESERVED_SKILL_PREFIXES:
            name = f"{prefix}test"
            error = SkillLoader._validate_name(name, name)
            assert error is not None, f"Reserved prefix '{prefix}' should be rejected"


class TestSkillLoaderScanSkillDir:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_skill_dir(self):
        skill_dir = os.path.join(self.tmpdir, "test-skill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: test-skill\ndescription: A test skill\n---\n\nBody content here")

        result = SkillLoader._scan_skill_dir(skill_dir)
        assert result is not None
        assert result.meta.name == "test-skill"
        assert result.meta.description == "A test skill"
        assert result.full_content.strip() == "Body content here"

    def test_missing_skill_md(self):
        skill_dir = os.path.join(self.tmpdir, "test-skill")
        os.makedirs(skill_dir)
        result = SkillLoader._scan_skill_dir(skill_dir)
        assert result is None

    def test_missing_required_fields(self):
        skill_dir = os.path.join(self.tmpdir, "test-skill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: test-skill\n---\n\nBody")  # missing description

        result = SkillLoader._scan_skill_dir(skill_dir)
        assert result is None  # should skip

    def test_name_dirname_mismatch(self):
        skill_dir = os.path.join(self.tmpdir, "dir-name")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: different-name\ndescription: desc\n---\n\nBody")

        result = SkillLoader._scan_skill_dir(skill_dir)
        assert result is None  # should skip

    def test_skill_with_allowed_tools(self):
        skill_dir = os.path.join(self.tmpdir, "web-research")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write('---\nname: web-research\ndescription: Research\nallowed_tools: ["web_search", "fetch_url"]\n---\n\nBody')

        result = SkillLoader._scan_skill_dir(skill_dir)
        assert result is not None
        assert result.meta.allowed_tools == ["web_search", "fetch_url"]

    def test_skill_with_scripts_and_references(self):
        skill_dir = os.path.join(self.tmpdir, "test-skill")
        os.makedirs(skill_dir)
        # Create SKILL.md
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: test-skill\ndescription: desc\n---\n\nBody")
        # Create a script
        with open(os.path.join(skill_dir, "helper.py"), "w") as f:
            f.write("print('hello')")
        # Create a reference
        with open(os.path.join(skill_dir, "guide.md"), "w") as f:
            f.write("# Guide")
        # Create an asset
        with open(os.path.join(skill_dir, "data.json"), "w") as f:
            f.write("{}")

        result = SkillLoader._scan_skill_dir(skill_dir)
        assert result is not None
        assert "helper.py" in result.scripts
        assert "guide.md" in result.references
        assert "data.json" in result.assets


class TestSkillLoaderDiscover:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_discover_single_dir(self):
        skill_dir = os.path.join(self.tmpdir, "test-skill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: test-skill\ndescription: A test\n---\n\nBody")

        results = SkillLoader.discover([self.tmpdir])
        assert len(results) == 1
        assert results[0].meta.name == "test-skill"

    def test_discover_empty_dir(self):
        results = SkillLoader.discover([self.tmpdir])
        assert results == []

    def test_discover_nonexistent_dir(self):
        results = SkillLoader.discover(["/nonexistent/path"])
        assert results == []

    def test_discover_multiple_skills(self):
        for name in ["skill-a", "skill-b", "skill-c"]:
            skill_dir = os.path.join(self.tmpdir, name)
            os.makedirs(skill_dir)
            with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
                f.write(f"---\nname: {name}\ndescription: desc\n---\n\nBody")

        results = SkillLoader.discover([self.tmpdir])
        assert len(results) == 3

    def test_discover_override_same_name(self):
        """Later directories override earlier ones for the same skill name."""
        dir1 = os.path.join(self.tmpdir, "dir1")
        dir2 = os.path.join(self.tmpdir, "dir2")
        os.makedirs(dir1)
        os.makedirs(dir2)

        # Both have a skill named "test-skill"
        for d in [dir1, dir2]:
            skill_dir = os.path.join(d, "test-skill")
            os.makedirs(skill_dir)
            with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
                f.write(f"---\nname: test-skill\ndescription: from {d}\n---\n\nBody from {d}")

        results = SkillLoader.discover([dir1, dir2])
        assert len(results) == 1
        # dir2 should override dir1
        assert "dir2" in results[0].meta.description

    def test_discover_skips_invalid(self):
        """Invalid skills are skipped, valid ones are still discovered."""
        # Valid skill
        valid_dir = os.path.join(self.tmpdir, "valid-skill")
        os.makedirs(valid_dir)
        with open(os.path.join(valid_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: valid-skill\ndescription: valid\n---\n\nBody")

        # Invalid skill (no SKILL.md)
        invalid_dir = os.path.join(self.tmpdir, "invalid-skill")
        os.makedirs(invalid_dir)

        results = SkillLoader.discover([self.tmpdir])
        assert len(results) == 1
        assert results[0].meta.name == "valid-skill"


# ======================================================================
# SkillRegistry tests
# ======================================================================

class TestSkillRegistry:
    def _make_skill(self, name="test-skill", desc="A test skill", tools=None):
        meta = SkillMeta(name=name, description=desc, allowed_tools=tools or [])
        return SkillDef(meta=meta, skill_dir="/tmp/test", full_content="Test content")

    def test_register_and_get(self):
        registry = SkillRegistry()
        skill = self._make_skill("hello")
        registry.register(skill)
        assert registry.get("hello") is skill

    def test_get_nonexistent(self):
        registry = SkillRegistry()
        assert registry.get("nonexistent") is None

    def test_list_all(self):
        registry = SkillRegistry()
        registry.register(self._make_skill("b-skill"))
        registry.register(self._make_skill("a-skill"))
        names = [s.meta.name for s in registry.list_all()]
        assert names == ["a-skill", "b-skill"]  # sorted

    def test_register_overwrite(self):
        registry = SkillRegistry()
        registry.register(self._make_skill("test", desc="v1"))
        registry.register(self._make_skill("test", desc="v2"))
        assert registry.get("test").meta.description == "v2"

    def test_format_descriptions_empty(self):
        registry = SkillRegistry()
        assert registry.format_descriptions() == ""

    def test_format_descriptions_basic(self):
        registry = SkillRegistry()
        registry.register(self._make_skill("web-research", desc="Research the web"))
        result = registry.format_descriptions()
        assert "web-research: Research the web" in result
        assert "tools:" not in result  # no allowed_tools

    def test_format_descriptions_with_tools(self):
        registry = SkillRegistry()
        registry.register(self._make_skill("web-research", desc="Research", tools=["web_search", "fetch_url"]))
        result = registry.format_descriptions()
        assert "web-research: Research [tools: web_search, fetch_url]" in result

    def test_load_full_content(self):
        registry = SkillRegistry()
        skill = self._make_skill("test")
        registry.register(skill)
        content = registry.load_full_content("test")
        assert content == "Test content"

    def test_load_full_content_not_found(self):
        registry = SkillRegistry()
        assert registry.load_full_content("nonexistent") is None

    def test_load_full_content_truncation(self):
        registry = SkillRegistry()
        long_content = "x" * 100000  # Much longer than default token limit
        meta = SkillMeta(name="test", description="test")
        skill = SkillDef(meta=meta, skill_dir="/tmp/test", full_content=long_content)
        registry.register(skill)
        content = registry.load_full_content("test")
        assert len(content) < len(long_content)
        assert "[Content truncated" in content

    def test_len_and_contains(self):
        registry = SkillRegistry()
        registry.register(self._make_skill("test"))
        assert len(registry) == 1
        assert "test" in registry
        assert "other" not in registry


# ======================================================================
# SkillActivationTool tests
# ======================================================================

class TestSkillActivationTool:
    def _make_registry_with_skill(self, name="test-skill", desc="A test", tools=None, content="Skill content"):
        registry = SkillRegistry()
        meta = SkillMeta(name=name, description=desc, allowed_tools=tools or [])
        skill = SkillDef(meta=meta, skill_dir="/tmp/test", full_content=content)
        registry.register(skill)
        return registry

    @pytest.mark.asyncio
    async def test_activate_skill(self):
        registry = self._make_registry_with_skill()
        events = []
        tool = SkillActivationTool(registry=registry, on_event=lambda e, d: events.append((e, d)))
        result = await tool.execute(skill_name="test-skill")
        assert "Skill Activated: test-skill" in result
        assert "Skill content" in result
        assert any(e == "skill_activated" for e, _ in events)

    @pytest.mark.asyncio
    async def test_activate_nonexistent_skill(self):
        registry = SkillRegistry()
        events = []
        tool = SkillActivationTool(registry=registry, on_event=lambda e, d: events.append((e, d)))
        result = await tool.execute(skill_name="nonexistent")
        assert "Error:" in result
        assert any(e == "skill_activation_failed" for e, _ in events)

    @pytest.mark.asyncio
    async def test_activate_empty_name(self):
        registry = self._make_registry_with_skill()
        tool = SkillActivationTool(registry=registry)
        result = await tool.execute(skill_name="")
        assert "Error:" in result

    @pytest.mark.asyncio
    async def test_max_activations(self):
        registry = self._make_registry_with_skill()
        tool = SkillActivationTool(registry=registry, max_activations=1)
        # First activation succeeds
        result1 = await tool.execute(skill_name="test-skill")
        assert "Error:" not in result1
        # Second activation fails
        result2 = await tool.execute(skill_name="test-skill")
        assert "Error:" in result2
        assert "Maximum" in result2 or "Max" in result2

    @pytest.mark.asyncio
    async def test_reset_task_state(self):
        registry = self._make_registry_with_skill()
        tool = SkillActivationTool(registry=registry, max_activations=1)
        await tool.execute(skill_name="test-skill")
        assert tool.activation_count == 1
        tool.reset_task_state()
        assert tool.activation_count == 0
        assert tool.active_skills == []

    @pytest.mark.asyncio
    async def test_tool_filter_callback(self):
        """v20.2: tool_filter_callback is called when skill has allowed_tools."""
        registry = self._make_registry_with_skill(tools=["web_search", "fetch_url"])
        filter_calls = []
        tool = SkillActivationTool(
            registry=registry,
            tool_filter_callback=lambda tools: filter_calls.append(tools),
        )
        await tool.execute(skill_name="test-skill")
        assert len(filter_calls) == 1
        assert filter_calls[0] == ["web_search", "fetch_url"]

    @pytest.mark.asyncio
    async def test_tool_filter_callback_not_called_for_empty_tools(self):
        """v20.2: callback NOT called when skill has no allowed_tools."""
        registry = self._make_registry_with_skill()  # no allowed_tools
        filter_calls = []
        tool = SkillActivationTool(
            registry=registry,
            tool_filter_callback=lambda tools: filter_calls.append(tools),
        )
        await tool.execute(skill_name="test-skill")
        assert len(filter_calls) == 0

    @pytest.mark.asyncio
    async def test_duplicate_activation_returns_cached(self):
        """Re-activating same skill returns cached result without incrementing count."""
        registry = self._make_registry_with_skill()
        tool = SkillActivationTool(registry=registry, max_activations=2)
        # First activation
        result1 = await tool.execute(skill_name="test-skill")
        assert "Skill Activated" in result1
        assert tool.activation_count == 1
        # Second activation of same skill — should return cached, not waste slot
        result2 = await tool.execute(skill_name="test-skill")
        assert "Already Active" in result2
        assert tool.activation_count == 1  # Count NOT incremented
        # Can still activate a different skill (up to limit)

    @pytest.mark.asyncio
    async def test_active_skills_tracking(self):
        registry = self._make_registry_with_skill()
        tool = SkillActivationTool(registry=registry, max_activations=3)
        await tool.execute(skill_name="test-skill")
        assert tool.active_skills == ["test-skill"]

    def test_name_and_description(self):
        registry = self._make_registry_with_skill()
        tool = SkillActivationTool(registry=registry)
        assert tool.name == "activate_skill"
        assert "activate" in tool.description.lower()
        assert "test-skill" in tool.description  # available skills listed

    def test_parameters_schema(self):
        registry = self._make_registry_with_skill()
        tool = SkillActivationTool(registry=registry)
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        assert "skill_name" in schema["properties"]
        assert "skill_name" in schema["required"]

    def test_to_openai_tool(self):
        registry = self._make_registry_with_skill()
        tool = SkillActivationTool(registry=registry)
        oai = tool.to_openai_tool()
        assert oai["type"] == "function"
        assert oai["function"]["name"] == "activate_skill"


# ======================================================================
# Skill guidance injection tests (prompt_utils)
# ======================================================================

class TestSkillGuidance:
    def test_skill_guidance_disabled_by_default(self):
        """SKILLS_ENABLED=false → get_skill_guidance returns empty string."""
        from agents.prompt_utils import get_skill_guidance
        original = config.SKILLS_ENABLED
        try:
            config.SKILLS_ENABLED = False
            assert get_skill_guidance() == ""
        finally:
            config.SKILLS_ENABLED = original

    def test_set_skill_descriptions(self):
        """set_skill_descriptions() sets module-level variable."""
        from agents.prompt_utils import set_skill_descriptions
        import agents.prompt_utils as pu
        # autouse fixture handles cleanup / autouse fixture 负责清理
        set_skill_descriptions("- test: A test skill")
        assert pu._SKILL_DESCRIPTIONS == "- test: A test skill"

    def test_skill_guidance_enabled_with_descriptions(self):
        """When SKILLS_ENABLED=true AND descriptions set, guidance is injected."""
        from agents.prompt_utils import set_skill_descriptions, get_skill_guidance
        import agents.prompt_utils as pu
        original_enabled = config.SKILLS_ENABLED
        try:
            config.SKILLS_ENABLED = True
            set_skill_descriptions("- test: A test skill")
            guidance = get_skill_guidance()
            assert "activate_skill" in guidance
            assert str(config.SKILLS_MAX_ACTIVATIONS_PER_TASK) in guidance
        finally:
            config.SKILLS_ENABLED = original_enabled

    def test_build_system_prompt_skill_guidance_param(self):
        """build_system_prompt() accepts inject_skill_guidance parameter."""
        from agents.prompt_utils import build_system_prompt
        # Default: inject_skill_guidance=True (should not error)
        result = build_system_prompt("Base prompt", inject_skill_guidance=True)
        assert "Base prompt" in result
        # Explicit False: should not error
        result2 = build_system_prompt("Base prompt", inject_skill_guidance=False)
        assert "Base prompt" in result2


# ======================================================================
# ReActEngine set_allowed_tools tests
# ======================================================================

class TestReActEngineToolFilter:
    def test_set_allowed_tools_basic(self):
        """set_allowed_tools() filters tool set."""
        from react.engine import ReActEngine
        from tools.web_search import WebSearchTool
        from tools.fetch_url import FetchUrlTool

        web = WebSearchTool()
        fetch = FetchUrlTool()
        engine = ReActEngine(
            llm_client=None,
            tools=[web, fetch],
            agent_name="test",
        )

        # Initially both tools are available
        assert "web_search" in engine.tools
        assert "fetch_url" in engine.tools

        # Filter to only web_search
        engine.set_allowed_tools(["web_search"])
        assert "web_search" in engine.tools
        assert "fetch_url" not in engine.tools

        # Only web_search schema remains
        assert len(engine.tool_schemas) == 1
        assert engine.tool_schemas[0]["function"]["name"] == "web_search"

    def test_set_allowed_tools_restore(self):
        """set_allowed_tools(None) restores full tool set."""
        from react.engine import ReActEngine
        from tools.web_search import WebSearchTool
        from tools.fetch_url import FetchUrlTool

        engine = ReActEngine(
            llm_client=None,
            tools=[WebSearchTool(), FetchUrlTool()],
            agent_name="test",
        )

        # Filter then restore
        engine.set_allowed_tools(["web_search"])
        assert len(engine.tools) == 1

        engine.set_allowed_tools(None)
        assert len(engine.tools) == 2
        assert "web_search" in engine.tools
        assert "fetch_url" in engine.tools

    def test_set_allowed_tools_unknown_names_ignored(self):
        """Unknown tool names in allowed_tools are silently ignored."""
        from react.engine import ReActEngine
        from tools.web_search import WebSearchTool

        engine = ReActEngine(
            llm_client=None,
            tools=[WebSearchTool()],
            agent_name="test",
        )

        engine.set_allowed_tools(["web_search", "nonexistent_tool"])
        assert len(engine.tools) == 1
        assert "web_search" in engine.tools

    def test_tools_full_backup(self):
        """_tools_full is a backup of the original tool set."""
        from react.engine import ReActEngine
        from tools.web_search import WebSearchTool
        from tools.fetch_url import FetchUrlTool

        engine = ReActEngine(
            llm_client=None,
            tools=[WebSearchTool(), FetchUrlTool()],
            agent_name="test",
        )

        assert "web_search" in engine._tools_full
        assert "fetch_url" in engine._tools_full
        assert len(engine._tools_full) == 2

    def test_set_allowed_tools_empty_list_restores_full(self):
        """Empty list [] is treated as 'no filter' and restores full tool set."""
        from react.engine import ReActEngine
        from tools.web_search import WebSearchTool
        from tools.fetch_url import FetchUrlTool

        engine = ReActEngine(
            llm_client=None,
            tools=[WebSearchTool(), FetchUrlTool()],
            agent_name="test",
        )

        # Filter first
        engine.set_allowed_tools(["web_search"])
        assert len(engine.tools) == 1

        # Empty list restores full set (same as None)
        engine.set_allowed_tools([])
        assert len(engine.tools) == 2
        assert len(engine.tool_schemas) == 2


# ======================================================================
# v20 Fix 1: YAML frontmatter features (folded/literal scalars, nested maps)
# v20 修复 1：YAML frontmatter 特性（折叠/字面量标量、嵌套映射）
# ======================================================================

class TestFrontmatterYAMLFeatures:
    """Real-YAML parsing of constructs the old line parser could not handle."""

    def test_folded_scalar_description(self):
        """`description: >` folded scalar yields the full joined text, not '>'."""
        content = (
            "---\n"
            "name: demo\n"
            "description: >\n"
            "  Review Python code for bugs and security issues.\n"
            "  Use when asked to review or audit Python code.\n"
            "---\n\nBody"
        )
        result, _ = SkillLoader._parse_frontmatter(content)
        assert result["description"].startswith("Review Python code for bugs")
        assert "audit Python code" in result["description"]
        assert result["description"] != ">"

    def test_literal_scalar_description(self):
        """`description: |` literal block scalar is parsed as multi-line text."""
        content = (
            "---\n"
            "name: demo\n"
            "description: |\n"
            "  Line one.\n"
            "  Line two.\n"
            "---\n\nBody"
        )
        result, _ = SkillLoader._parse_frontmatter(content)
        assert "Line one." in result["description"]
        assert "Line two." in result["description"]

    def test_nested_metadata_block(self):
        """Nested `metadata:` block mapping is parsed into a dict, not dropped."""
        content = (
            "---\n"
            "name: demo\n"
            "description: A demo skill\n"
            "metadata:\n"
            "  author: manus-demo\n"
            "  version: \"1.0\"\n"
            "allowed-tools: web_search fetch_url\n"
            "---\n\nBody"
        )
        result, _ = SkillLoader._parse_frontmatter(content)
        assert isinstance(result["metadata"], dict)
        assert result["metadata"]["author"] == "manus-demo"
        assert result["metadata"]["version"] == "1.0"
        # hyphenated spec key normalized + space-separated split
        assert result["allowed_tools"] == ["web_search", "fetch_url"]
        # nested keys must NOT leak to top level
        assert "author" not in result
        assert "version" not in result

    def test_description_with_colon_not_polluted(self):
        """A description containing a colon stays one value (no stray keys)."""
        content = (
            "---\n"
            "name: demo\n"
            "description: \"Use when: researching a topic online\"\n"
            "---\n\nBody"
        )
        result, _ = SkillLoader._parse_frontmatter(content)
        assert result["description"] == "Use when: researching a topic online"
        assert "Use when" not in result  # not turned into a key

    def test_malformed_yaml_falls_back_to_line_parser(self):
        """Invalid YAML degrades to the line parser instead of crashing."""
        content = (
            "---\n"
            "name: demo\n"
            "description: [unclosed\n"
            "foo: bar\n"
            "---\n\nBody"
        )
        # Must not raise; line parser still extracts simple key:value pairs.
        result, body_start = SkillLoader._parse_frontmatter(content)
        assert result.get("name") == "demo"
        assert result.get("foo") == "bar"
        assert body_start > 0

    def test_full_scan_with_folded_description(self, tmp_path):
        """End-to-end: a skill authored with a folded description is discoverable."""
        skill_dir = tmp_path / "folded-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: folded-skill\n"
            "description: >\n"
            "  Does something useful across multiple lines.\n"
            "  Activate for the useful thing.\n"
            "metadata:\n"
            "  author: tester\n"
            "---\n\n# Body\n",
            encoding="utf-8",
        )
        skill = SkillLoader._scan_skill_dir(str(skill_dir))
        assert skill is not None
        assert skill.meta.name == "folded-skill"
        assert "Does something useful" in skill.meta.description
        assert skill.meta.metadata.get("author") == "tester"


# ======================================================================
# v20 Fix 2 + Fix 5: skill tool-filter union + parallel-DAG guard
# v20 修复 2 + 修复 5：技能工具过滤并集 + 并行 DAG 防护
# ======================================================================

class TestSkillToolFilterUnionAndParallelGuard:
    """Exercise OrchestratorAgent._apply_skill_tool_filter in isolation.

    The method only touches a handful of attributes on ``self``, so we bind it
    to a lightweight SimpleNamespace stand-in (avoids constructing a full
    OrchestratorAgent + LLMClient).
    """

    def _build_fixture(self):
        import types
        from react.engine import ReActEngine
        from tools.web_search import WebSearchTool
        from tools.fetch_url import FetchUrlTool
        from tools.code_executor import CodeExecutorTool
        from tools.file_ops import FileOpsTool

        # Two skills with disjoint allowed_tools
        skill_a = SkillDef(
            meta=SkillMeta(name="skill-a", description="A", allowed_tools=["web_search", "fetch_url"]),
            skill_dir="/tmp/skill-a", full_content="A",
        )
        skill_b = SkillDef(
            meta=SkillMeta(name="skill-b", description="B", allowed_tools=["execute_python"]),
            skill_dir="/tmp/skill-b", full_content="B",
        )
        registry = SkillRegistry()
        registry.register(skill_a)
        registry.register(skill_b)

        activation_tool = SkillActivationTool(registry=registry)

        tools = [
            WebSearchTool(), FetchUrlTool(), CodeExecutorTool(), FileOpsTool(),
            activation_tool,
        ]
        engine = ReActEngine(llm_client=None, tools=tools, agent_name="exec")

        fake_self = types.SimpleNamespace(
            _skill_activation_tool=activation_tool,
            _skill_registry=registry,
            executor_agent=types.SimpleNamespace(
                tools={t.name: t for t in tools},
                _react_engine=engine,
            ),
            emergent_planner=types.SimpleNamespace(_react_engine=None),
            goal_driven_planner=None,
            _on_event=lambda *a: None,
            _active_skill_tools=None,
        )
        return fake_self, activation_tool, engine

    def test_union_across_active_skills(self):
        """Activating skill-b after skill-a keeps skill-a's tools (no last-wins drop)."""
        from agents.orchestrator import OrchestratorAgent

        fake_self, activation_tool, engine = self._build_fixture()
        # Both skills are active (skill-a then skill-b) — activation order already applied.
        activation_tool._active_skills = ["skill-a", "skill-b"]

        # Simulate skill-b's activation triggering the filter callback.
        OrchestratorAgent._apply_skill_tool_filter(fake_self, ["execute_python"])

        names = set(engine.tools.keys())
        # skill-a's tools must survive the union
        assert "web_search" in names
        assert "fetch_url" in names
        # skill-b's tool present
        assert "execute_python" in names
        # activate_skill meta-tool always preserved
        assert "activate_skill" in names
        # tool NOT in any active skill's allowed_tools is filtered out
        assert "file_ops" not in names

    def test_single_skill_filter(self):
        """One active skill restricts to its allowed_tools + activate_skill."""
        from agents.orchestrator import OrchestratorAgent

        fake_self, activation_tool, engine = self._build_fixture()
        activation_tool._active_skills = ["skill-a"]
        OrchestratorAgent._apply_skill_tool_filter(fake_self, ["web_search", "fetch_url"])

        names = set(engine.tools.keys())
        assert names == {"web_search", "fetch_url", "activate_skill"}

    def test_parallel_dag_skips_engine_mutation(self, monkeypatch):
        """With DAG_SERIAL_EXECUTION=false the shared engine tool set is untouched."""
        from agents.orchestrator import OrchestratorAgent

        monkeypatch.setattr(config, "DAG_SERIAL_EXECUTION", False)
        fake_self, activation_tool, engine = self._build_fixture()
        events = []
        fake_self._on_event = lambda ev, data=None: events.append((ev, data))
        activation_tool._active_skills = ["skill-a"]

        full_before = set(engine.tools.keys())
        OrchestratorAgent._apply_skill_tool_filter(fake_self, ["web_search", "fetch_url"])

        # Engine NOT narrowed (all tools still present)
        assert set(engine.tools.keys()) == full_before
        # A skip event was emitted
        assert any(ev == "skill_tool_filter_skipped" for ev, _ in events)


# ======================================================================
# v20 Fix 3: distiller writes YAML-safe frontmatter that round-trips
# v20 修复 3：蒸馏器写出可往返的 YAML 安全 frontmatter
# ======================================================================

class TestDistillerFrontmatterRoundtrip:
    def test_render_frontmatter_roundtrips_adversarial_text(self):
        from evolution.skill_distiller import SkillAutoDistiller

        fm = {
            "name": "auto-research-ai",
            "description": 'Research: AI trends, with "quotes" and: colons\nand a newline',
            "metadata": {
                "author": "auto-distilled",
                "version": "1.0",
                "source": "self-evolution",
                "distilled_from": 'Find: X: Y "z"',
            },
        }
        rendered = SkillAutoDistiller._render_frontmatter(fm)
        full = "---\n" + rendered + "---\n\n# Body\n"
        parsed, body_start = SkillLoader._parse_frontmatter(full)

        assert parsed["name"] == "auto-research-ai"
        assert parsed["description"].startswith("Research: AI trends")
        assert isinstance(parsed["metadata"], dict)
        assert parsed["metadata"]["source"] == "self-evolution"
        assert parsed["metadata"]["distilled_from"] == 'Find: X: Y "z"'
        assert body_start > 0
