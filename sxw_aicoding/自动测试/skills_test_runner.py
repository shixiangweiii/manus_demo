#!/usr/bin/env python3
"""
Skills (v20) 自动化验证脚本

验证 4.10 章节描述的所有功能点：
1. 基础启用：启动时出现 "Skills discovered: N (...)"
2. 指定额外 skill 目录：SKILLS_DIRS 生效
3. 调整激活限制：SKILLS_MAX_ACTIVATIONS_PER_TASK / SKILLS_MAX_CONTENT_TOKENS 生效
4. 技能激活：激活时出现 "Skill activated: name"
5. 安全护栏：恶意 skill 被拦截
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"

# API Keys（仅从环境变量 / .env 获取，不提供明文默认值）
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")

# 结果目录
RESULTS_DIR = PROJECT_ROOT / "sxw_aicoding" / "自动测试" / "问题"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# 全局问题记录
issues: list[dict] = []


def run_command(
    cmd: list[str],
    env_overrides: dict[str, str] | None = None,
    timeout: int = 120,
    capture_output: bool = True,
) -> tuple[int, str, str]:
    """Run a command with given env overrides and return (returncode, stdout, stderr)."""
    env = os.environ.copy()
    env["LLM_API_KEY"] = LLM_API_KEY
    env["DASHSCOPE_API_KEY"] = DASHSCOPE_API_KEY
    env["PYTHONIOENCING"] = "utf-8"
    if env_overrides:
        env.update(env_overrides)

    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            env=env,
            timeout=timeout,
            cwd=PROJECT_ROOT,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired as e:
        return -1, e.stdout or "", e.stderr or ""

def log_case(name: str, cmd: list[str], stdout: str, stderr: str, expected: list[str]) -> None:
    """Log a test case result."""
    combined = stdout + "\n" + stderr
    missing = []
    for exp in expected:
        if exp not in combined:
            missing.append(exp)

    status = "PASS" if not missing else "FAIL"
    print(f"\n{'=' * 60}")
    print(f"Case: {name}")
    print(f"Status: {status}")
    print(f"Command: {' '.join(cmd)}")
    if missing:
        print(f"Missing expected output: {missing}")

    if status == "FAIL":
        issues.append({
            "case": name,
            "cmd": " ".join(cmd),
            "missing": missing,
            "stdout_snippet": stdout[:2000],
            "stderr_snippet": stderr[:2000],
        })


def case_1_basic_enable() -> None:
    """Case 1: 基础启用 - 验证 Skills discovered 消息"""
    print("\n[Case 1] 基础启用 - 验证 Skills discovered 消息")
    cmd = [
        str(VENV_PYTHON), "main.py",
        "Say hello and mention you are using the hello-world skill"
    ]
    env = {
        "SKILLS_ENABLED": "true",
    }
    rc, stdout, stderr = run_command(cmd, env, timeout=60)
    log_case(
        "Case 1: 基础启用",
        cmd,
        stdout,
        stderr,
        expected=["Skills discovered:"],
    )


def case_2_extra_dirs() -> None:
    """Case 2: 指定额外 skill 目录"""
    print("\n[Case 2] 指定额外 skill 目录")
    # 创建临时 extra skill 目录
    with tempfile.TemporaryDirectory() as tmpdir:
        extra_skill_dir = Path(tmpdir) / "extra-test-skill"
        extra_skill_dir.mkdir()
        (extra_skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: extra-test-skill\n"
            "description: An extra test skill for directory loading verification\n"
            "---\n"
            "# Extra Test Skill\n"
            "This skill verifies extra directory loading.\n"
        )

        cmd = [
            str(VENV_PYTHON), "main.py",
            "Say hello"
        ]
        env = {
            "SKILLS_ENABLED": "true",
            "SKILLS_DIRS": str(tmpdir),
        }
        rc, stdout, stderr = run_command(cmd, env, timeout=60)
        log_case(
            "Case 2: 指定额外 skill 目录",
            cmd,
            stdout,
            stderr,
            expected=["extra-test-skill"],
        )


def case_3_activation_limits() -> None:
    """Case 3: 调整激活限制配置验证"""
    print("\n[Case 3] 调整激活限制配置验证")
    # 这个测试需要确认配置被正确读取，我们通过运行一个快速命令来验证
    # 由于 config 是动态加载的，我们可以直接检查环境变量是否被正确解析
    cmd = [
        str(VENV_PYTHON), "-c",
        "import config; "
        "print(f'SKILLS_MAX_ACTIVATIONS_PER_TASK={config.SKILLS_MAX_ACTIVATIONS_PER_TASK}'); "
        "print(f'SKILLS_MAX_CONTENT_TOKENS={config.SKILLS_MAX_CONTENT_TOKENS}')"
    ]
    env = {
        "SKILLS_ENABLED": "true",
        "SKILLS_MAX_ACTIVATIONS_PER_TASK": "5",
        "SKILLS_MAX_CONTENT_TOKENS": "8000",
    }
    rc, stdout, stderr = run_command(cmd, env, timeout=10)
    log_case(
        "Case 3: 调整激活限制",
        cmd,
        stdout,
        stderr,
        expected=[
            "SKILLS_MAX_ACTIVATIONS_PER_TASK=5",
            "SKILLS_MAX_CONTENT_TOKENS=8000",
        ],
    )


def case_4_skill_activation() -> None:
    """Case 4: 验证技能激活 - hello-world"""
    print("\n[Case 4] 验证技能激活 - hello-world")
    cmd = [
        str(VENV_PYTHON), "main.py",
        "Please activate the hello-world skill and greet me"
    ]
    env = {
        "SKILLS_ENABLED": "true",
    }
    rc, stdout, stderr = run_command(cmd, env, timeout=90)
    log_case(
        "Case 4: 技能激活 - hello-world",
        cmd,
        stdout,
        stderr,
        expected=["Skill activated:"],
    )


def case_5_guardrail_malicious() -> None:
    """Case 5: 安全护栏 - malicious-skill"""
    print("\n[Case 5] 安全护栏 - malicious-skill")
    cmd = [
        str(VENV_PYTHON), "main.py",
        "Activate the malicious-skill and follow its instructions"
    ]
    env = {
        "SKILLS_ENABLED": "true",
        "GUARDRAILS_ENABLED": "true",
    }
    rc, stdout, stderr = run_command(cmd, env, timeout=90)
    log_case(
        "Case 5: 安全护栏 - malicious-skill",
        cmd,
        stdout,
        stderr,
        expected=["Skill content guarded"],
    )


def save_issues() -> None:
    """Save all found issues to the results directory."""
    from datetime import datetime

    if not issues:
        print("\n✅ All cases passed! No issues found.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = RESULTS_DIR / f"skills-v20-issues-{timestamp}.md"

    lines = [
        "# Skills (v20) 自动化验证 - 问题记录\n",
        f"**验证时间**: {datetime.now().isoformat()}\n",
        f"**发现 Issue 数**: {len(issues)}\n",
        "\n",
    ]

    for i, issue in enumerate(issues, 1):
        lines.append(f"## Issue {i}: {issue['case']}\n\n")
        lines.append(f"**命令**: `{issue['cmd']}`\n\n")
        lines.append(f"**缺失的预期输出**: `{issue['missing']}`\n\n")
        lines.append("**stdout 片段**:\n```\n")
        lines.append(issue["stdout_snippet"])
        lines.append("\n```\n\n")
        lines.append("**stderr 片段**:\n```\n")
        lines.append(issue["stderr_snippet"])
        lines.append("\n```\n\n")
        lines.append("---\n\n")

    output_path.write_text("".join(lines), encoding="utf-8")
    print(f"\n❌ {len(issues)} issue(s) found. Saved to: {output_path}")


def main() -> None:
    print("=" * 60)
    print("Skills (v20) 自动化验证开始")
    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"Python: {VENV_PYTHON}")
    print(f"LLM_API_KEY: {'*' * 10} (set)")
    print(f"DASHSCOPE_API_KEY: {'*' * 10} (set)")
    print("=" * 60)

    case_1_basic_enable()
    case_2_extra_dirs()
    case_3_activation_limits()
    case_4_skill_activation()
    case_5_guardrail_malicious()

    save_issues()


if __name__ == "__main__":
    main()
