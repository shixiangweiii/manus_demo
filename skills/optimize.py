"""
CLI for the Skill optimization loop.

Usage:
  python -m skills.optimize --skill .agents/skills/hello-world
  python -m skills.optimize --skill .agents/skills/hello-world --results results.json
  python -m skills.optimize --skill .agents/skills/hello-world --results results.json --apply

默认只打印 diff，不写入文件；`--apply` 才会写入。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from skills.optimizer import SkillOptimizer


def _resolve_skill_dir(skill_arg: str) -> str:
    """Resolve a skill name or directory path to a skill directory."""
    if os.path.isdir(skill_arg):
        return os.path.abspath(skill_arg)

    import config
    candidates = [
        os.path.join(config.SKILLS_PROJECT_DIR, skill_arg),
        os.path.join(config.SKILLS_USER_DIR, skill_arg),
    ]
    if config.SKILLS_DIRS:
        for extra in config.SKILLS_DIRS.split(","):
            extra = extra.strip()
            if extra:
                candidates.append(os.path.join(os.path.expanduser(extra), skill_arg))
    for path in candidates:
        if os.path.isdir(path):
            return os.path.abspath(path)
    return os.path.abspath(skill_arg)


async def _main_async(args: argparse.Namespace) -> int:
    skill_dir = _resolve_skill_dir(args.skill)
    results = []
    if args.results:
        try:
            results = SkillOptimizer.load_eval_results(args.results)
        except Exception as exc:
            print(f"Error: cannot load results file {args.results}: {exc}", file=sys.stderr)
            return 2

    optimizer = SkillOptimizer(llm_client=None)
    report = await optimizer.optimize(skill_dir, results, apply=args.apply)
    if report is None:
        print(f"Error: cannot optimize skill at {skill_dir}", file=sys.stderr)
        return 1

    print(f"Skill: {report.skill_name}")
    print(f"Directory: {report.skill_dir}")
    print(f"Train cases: {len(report.train_cases)}")
    print(f"Validation cases: {len(report.validation_cases)}")
    print("\nDiagnosis:")
    print(report.diagnosis)
    print("\nDiff:")
    print(report.diff or "(no changes proposed)")
    if report.applied:
        print("\nApplied: yes")
    else:
        print("\nApplied: no (run with --apply to write proposed SKILL.md)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Optimize an Agent Skill description/content.")
    parser.add_argument("--skill", required=True, help="Skill name or path to skill directory containing SKILL.md")
    parser.add_argument("--results", default="", help="Optional evaluation JSON report with task results")
    parser.add_argument("--apply", action="store_true", help="Apply the proposed SKILL.md changes (default: diff only)")
    args = parser.parse_args(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
