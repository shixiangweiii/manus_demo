"""
Skill Loader (v20.1) - Discovers and parses skill packages from the filesystem.
技能加载器（v20.1）—— 从文件系统发现并解析技能包。

A skill package is a directory containing a SKILL.md file with YAML frontmatter.
The frontmatter provides SkillMeta fields (name, description, allowed_tools, etc.);
the body after the frontmatter is the full skill content.

技能包是包含 SKILL.md 文件的目录；YAML frontmatter 提供 SkillMeta 字段
（name, description, allowed_tools 等），frontmatter 之后的内容是技能完整内容。

Parse failures are logged as warnings and skipped — never crash the host.
解析失败时记录警告并跳过，绝不导致宿主崩溃。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import config

from skills.models import SkillDef, SkillMeta, SKILL_NAME_PATTERN, RESERVED_SKILL_PREFIXES

logger = logging.getLogger(__name__)

# SKILL.md frontmatter delimiter pattern / frontmatter 分隔符正则
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class SkillLoader:
    """
    Discovers skill packages in configured directories and parses them.
    在配置目录中发现技能包并解析。

    All methods are static (stateless) — the loader has no mutable state.
    The typical usage is: ``SkillLoader.discover(dirs)`` returns a list of
    valid ``SkillDef`` objects ready for registration.

    所有方法为静态（无状态）—— loader 没有可变状态。
    典型用法：``SkillLoader.discover(dirs)`` 返回有效的 ``SkillDef`` 列表。
    """

    @staticmethod
    def discover(skill_dirs: list[str]) -> list[SkillDef]:
        """Scan all skill directories, parse each SKILL.md, return valid SkillDefs.
        扫描所有技能目录，解析每个 SKILL.md，返回有效的 SkillDef 列表。

        Directories that don't exist are silently skipped. Parse failures within
        a valid directory are logged as warnings and the skill is skipped.

        不存在的目录被静默跳过。有效目录内的解析失败记为警告并跳过该技能。

        Args:
            skill_dirs: Ordered list of directories to scan. Later directories
                with the same skill name override earlier ones (user > project).
                有序目录列表。同名技能后者覆盖前者（user > project）。

        Returns:
            List of successfully parsed SkillDef objects.
            成功解析的 SkillDef 对象列表。
        """
        skills: dict[str, SkillDef] = {}  # name -> SkillDef, later dirs override

        for skill_dir in skill_dirs:
            if not os.path.isdir(skill_dir):
                logger.debug("[SkillLoader] Directory does not exist, skipping: %s", skill_dir)
                continue

            try:
                entries = os.listdir(skill_dir)
            except OSError as e:
                logger.warning("[SkillLoader] Cannot list directory %s: %s", skill_dir, e)
                continue

            for entry in sorted(entries):  # sorted for deterministic order
                entry_path = os.path.join(skill_dir, entry)
                if not os.path.isdir(entry_path):
                    continue

                skill = SkillLoader._scan_skill_dir(entry_path)
                if skill is not None:
                    # Later directories override earlier ones (same name)
                    if skill.meta.name in skills:
                        logger.debug(
                            "[SkillLoader] Skill '%s' overridden by later directory: %s",
                            skill.meta.name, entry_path,
                        )
                    skills[skill.meta.name] = skill

        result = list(skills.values())
        if result:
            logger.info(
                "[SkillLoader] Discovered %d skill(s): %s",
                len(result),
                ", ".join(s.meta.name for s in result),
            )
        else:
            logger.debug("[SkillLoader] No skills discovered in: %s", skill_dirs)

        return result

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict[str, Any], int]:
        """Parse YAML frontmatter from SKILL.md content.
        从 SKILL.md 内容中解析 YAML frontmatter。

        Uses a lightweight key: value parser (no PyYAML dependency).
        The frontmatter schema is small (6 fields), so a regex-based
        parser is sufficient and avoids adding a new dependency.

        使用轻量级 key: value 解析器（无 PyYAML 依赖）。
        frontmatter 模式很小（6 个字段），基于正则的解析器即可。

        Supports:
        - Simple key: value pairs (string values)
        - key: [JSON array] for list values (e.g., allowed_tools: ["t1", "t2"])
        - key: space/comma separated values for list fields (e.g., allowed_tools: t1, t2)
        - # comment lines (ignored)

        Args:
            content: Full SKILL.md file content.

        Returns:
            Tuple of (parsed key-value dict, body start position).
            Empty dict and 0 if no valid frontmatter found.
            The body start position avoids a second regex match in _scan_skill_dir.
        """
        match = _FRONTMATTER_RE.match(content)
        if not match:
            return {}, 0

        raw = match.group(1)
        result: dict[str, Any] = {}

        for line in raw.split("\n"):
            line = line.strip()
            # Skip empty lines and comments / 跳过空行和注释
            if not line or line.startswith("#"):
                continue

            colon_idx = line.find(":")
            if colon_idx < 0:
                continue  # Not a valid key: value line / 非有效 key: value 行

            key = line[:colon_idx].strip()
            value_str = line[colon_idx + 1:].strip()

            if not key:
                continue

            # Normalize hyphenated keys to underscored keys (spec uses "allowed-tools",
            # Python uses "allowed_tools"). This allows SKILL.md files to follow the
            # agentskills.io spec while keeping our data models idiomatic Python.
            # 将连字符键名规范化为下划线键名（规范用 "allowed-tools"，Python 用 "allowed_tools"）
            key = key.replace("-", "_")

            # Try JSON array parsing first / 先尝试 JSON 数组解析
            if value_str.startswith("["):
                try:
                    parsed = json.loads(value_str)
                    if isinstance(parsed, list):
                        result[key] = [str(v).strip() for v in parsed if v is not None]
                        continue
                except json.JSONDecodeError:
                    pass  # Fall through to string parsing / 回退到字符串解析

            # Try comma-separated or space-separated list parsing for list-type fields.
            # The agentskills.io spec uses space-separated: "allowed-tools: Bash Read"
            # Also support comma-separated as a convenience: "allowed_tools: web_search, fetch_url"
            # 为列表类字段尝试逗号分隔或空格分隔解析。
            # agentskills.io 规范用空格分隔："allowed-tools: Bash Read"
            # 也支持逗号分隔作为便利格式："allowed_tools: web_search, fetch_url"
            if key in ("allowed_tools", "tags"):
                if "," in value_str:
                    result[key] = [v.strip() for v in value_str.split(",") if v.strip()]
                elif value_str:
                    # Space-separated (spec format): "Bash(git:*) Read" -> ["Bash(git:*)", "Read"]
                    # 空格分隔（规范格式）："Bash(git:*) Read" -> ["Bash(git:*)", "Read"]
                    result[key] = value_str.split()
                continue

            # Simple string value / 简单字符串值
            # Strip surrounding quotes if present (YAML-style)
            # 去除两端的引号（YAML 风格）
            if len(value_str) >= 2 and value_str[0] == '"' and value_str[-1] == '"':
                value_str = value_str[1:-1]
            elif len(value_str) >= 2 and value_str[0] == "'" and value_str[-1] == "'":
                value_str = value_str[1:-1]
            result[key] = value_str

        return result, match.end()

    @staticmethod
    def _validate_name(name: str, dir_name: str) -> str | None:
        """Validate and normalize skill name. Returns error message or None.
        校验并规范化技能名称。返回错误消息或 None（校验通过时）。

        Rules:
        - Must be non-empty, kebab-case (lowercase, digits, hyphens), 1-64 chars
        - Must match directory name (prevent alias confusion)
        - Reserved prefixes are forbidden (prevent confusion with built-in tools)

        规则：
        - 非空，kebab-case（小写字母、数字、连字符），1-64 字符
        - 必须匹配目录名（防止别名混淆）
        - 禁止保留前缀（防止与内置工具混淆）
        """
        if not name:
            return "Skill name is empty"

        if not SKILL_NAME_PATTERN.match(name):
            return (
                f"Skill name '{name}' is invalid: must be kebab-case "
                f"(lowercase, digits, hyphens), 1-64 chars"
            )

        if name != dir_name:
            return (
                f"Skill name '{name}' does not match directory name '{dir_name}'"
            )

        for prefix in RESERVED_SKILL_PREFIXES:
            if name.startswith(prefix):
                return f"Skill name '{name}' uses reserved prefix '{prefix}'"

        return None  # Valid / 校验通过

    @staticmethod
    def _scan_skill_dir(skill_dir: str) -> SkillDef | None:
        """Parse a single skill directory. Returns None on any failure.
        解析单个技能目录。任何失败返回 None。

        Steps:
        1. Check SKILL.md exists
        2. Read SKILL.md content
        3. Parse frontmatter into dict
        4. Build SkillMeta from parsed frontmatter
        5. Validate skill name
        6. Classify assets (scripts, references, other)
        7. Build and return SkillDef
        """
        dir_name = os.path.basename(skill_dir)
        skill_md_path = os.path.join(skill_dir, "SKILL.md")

        # Step 1: Check SKILL.md exists / 检查 SKILL.md 是否存在
        if not os.path.isfile(skill_md_path):
            logger.debug("[SkillLoader] No SKILL.md in: %s", skill_dir)
            return None

        # Step 2: Read SKILL.md content / 读取 SKILL.md 内容
        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError) as e:
            # Catch both file-system errors and encoding errors (UnicodeDecodeError
            # inherits from ValueError, not OSError). "Never crash the host" principle.
            # 同时捕获文件系统错误和编码错误（UnicodeDecodeError 继承自 ValueError
            # 而非 OSError）。遵循"绝不导致宿主崩溃"原则。
            logger.warning("[SkillLoader] Cannot read %s: %s", skill_md_path, e)
            return None

        # Step 3: Parse frontmatter / 解析 frontmatter
        fm, body_start = SkillLoader._parse_frontmatter(content)
        if not fm:
            logger.warning(
                "[SkillLoader] No valid frontmatter in %s — skipping", skill_md_path
            )
            return None

        # Step 4: Extract required fields / 提取必填字段
        name = fm.get("name", "")
        description = fm.get("description", "")

        if not name:
            logger.warning(
                "[SkillLoader] Missing 'name' in frontmatter of %s — skipping",
                skill_md_path,
            )
            return None

        if not description:
            logger.warning(
                "[SkillLoader] Missing 'description' in frontmatter of %s — skipping",
                skill_md_path,
            )
            return None

        # Validate description length (spec: 1-1024 chars)
        # 校验描述长度（规范：1-1024 字符）
        if len(description) > 1024:
            logger.warning(
                "[SkillLoader] Description too long (%d chars, max 1024) in %s — truncating",
                len(description), skill_md_path,
            )
            description = description[:1024]

        # Step 5: Validate skill name / 校验技能名称
        error = SkillLoader._validate_name(str(name), dir_name)
        if error:
            logger.warning(
                "[SkillLoader] Invalid skill in %s: %s — skipping",
                skill_dir, error,
            )
            return None

        # Build SkillMeta / 构建 SkillMeta
        allowed_tools_raw = fm.get("allowed_tools", [])
        if isinstance(allowed_tools_raw, str):
            # Space-separated or comma-separated string fallback
            # 空格分隔或逗号分隔字符串回退
            if "," in allowed_tools_raw:
                allowed_tools_raw = [t.strip() for t in allowed_tools_raw.split(",") if t.strip()]
            else:
                allowed_tools_raw = allowed_tools_raw.split()

        # Auto-detect license/trust level from directory if not explicitly set
        # 如果未显式设置，根据目录自动检测 license/信任级别
        license_val = str(fm.get("license", "")).strip()
        if not license_val:
            # Infer from skill_dir: project dir → "project", user dir → "user", else → "third_party"
            # 从 skill_dir 推断：项目目录→"project"，用户目录→"user"，其他→"third_party"
            abs_dir = os.path.abspath(skill_dir)
            if config.SKILLS_PROJECT_DIR and abs_dir.startswith(os.path.abspath(config.SKILLS_PROJECT_DIR)):
                license_val = "project"
            elif config.SKILLS_USER_DIR and abs_dir.startswith(os.path.abspath(config.SKILLS_USER_DIR)):
                license_val = "user"
            else:
                license_val = "third_party"

        meta = SkillMeta(
            name=str(name),
            description=str(description),
            license=license_val,
            compatibility=str(fm.get("compatibility", ">=20.0")),
            metadata=fm.get("metadata", {}) if isinstance(fm.get("metadata"), dict) else {},
            allowed_tools=list(allowed_tools_raw) if isinstance(allowed_tools_raw, list) else [],
        )

        # Step 6: Extract body (after frontmatter) using position from _parse_frontmatter
        # 使用 _parse_frontmatter 返回的位置提取正文，避免二次正则匹配
        full_content = content[body_start:] if body_start > 0 else content

        # Step 7: Classify assets / 分类资源
        scripts, references, assets = SkillLoader._classify_assets(skill_dir)

        return SkillDef(
            meta=meta,
            skill_dir=os.path.abspath(skill_dir),
            full_content=full_content,
            scripts=scripts,
            references=references,
            assets=assets,
        )

    @staticmethod
    def _classify_assets(skill_dir: str) -> tuple[list[str], list[str], list[str]]:
        """Classify files in skill_dir into scripts, references, assets.
        将 skill_dir 中的文件分类为 scripts、references、assets。

        Heuristics / 启发式规则：
        - scripts: *.py, *.sh, *.js (executable)
        - references: *.md, *.txt, *.rst (documentation)
        - assets: everything else (images, data files, etc.)
        - SKILL.md is excluded (already parsed)

        Returns:
            (scripts, references, assets) — relative paths from skill_dir.
        """
        scripts: list[str] = []
        references: list[str] = []
        assets: list[str] = []

        script_exts = {".py", ".sh", ".js", ".ts"}
        reference_exts = {".md", ".txt", ".rst", ".adoc"}

        try:
            for root, dirs, files in os.walk(skill_dir):
                # Skip hidden directories / 跳过隐藏目录
                dirs[:] = [d for d in dirs if not d.startswith(".")]

                for fname in files:
                    if fname == "SKILL.md":
                        continue  # Already parsed / 已解析

                    rel_path = os.path.relpath(
                        os.path.join(root, fname), skill_dir
                    )

                    # Skip hidden files / 跳过隐藏文件
                    if fname.startswith("."):
                        continue

                    _, ext = os.path.splitext(fname)
                    if ext in script_exts:
                        scripts.append(rel_path)
                    elif ext in reference_exts:
                        references.append(rel_path)
                    else:
                        assets.append(rel_path)

        except OSError as e:
            logger.warning(
                "[SkillLoader] Cannot walk directory %s: %s", skill_dir, e
            )

        return scripts, references, assets
