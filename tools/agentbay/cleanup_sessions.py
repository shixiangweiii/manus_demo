"""List or delete AgentBay sessions created by Manus Demo tools.

Usage:
  python -m tools.agentbay.cleanup_sessions
  python -m tools.agentbay.cleanup_sessions --delete
"""

from __future__ import annotations

import argparse

from core.settings import get_settings
from tools.agentbay.runtime import get_agentbay_sdk, get_api_key, session_labels


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect AgentBay tool sessions.")
    parser.add_argument("--delete", action="store_true", help="Delete matching running sessions.")
    parser.add_argument("--status", default="RUNNING", help="Session status to list. Defaults to RUNNING.")
    args = parser.parse_args()

    settings = get_settings().capabilities
    api_key = get_api_key(settings)
    if not api_key:
        print("AGENTBAY_API_KEY is not configured.")
        return 1

    AgentBay, _, _, _ = get_agentbay_sdk(settings)
    agent_bay = AgentBay(api_key=api_key)
    labels = session_labels("")
    labels.pop("tool", None)
    result = agent_bay.list(labels=labels, limit=50, status=args.status)
    if not result.success:
        print(f"Error: {result.error_message}")
        return 1

    session_ids = list(getattr(result, "session_ids", []) or [])
    if not session_ids:
        print(f"No matching AgentBay sessions with status={args.status}.")
        return 0

    print(f"Matching AgentBay sessions ({args.status}):")
    for session_id in session_ids:
        print(f"- {session_id}")

    if not args.delete:
        print("Dry run only. Re-run with --delete to delete these sessions.")
        return 0

    # The SDK delete API requires a Session object; fetch each session detail.
    for session_id in session_ids:
        detail = agent_bay.get(session_id)
        if not detail.success:
            print(f"Failed to fetch {session_id}: {detail.error_message}")
            continue
        delete_result = agent_bay.delete(detail.session)
        ok = getattr(delete_result, "success", True)
        print(f"{'Deleted' if ok else 'Delete requested/failed'}: {session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
