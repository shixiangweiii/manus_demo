"""Minimal AgentBay SDK smoke test.

Reads AGENTBAY_API_KEY from the environment, creates one default session, and
deletes it immediately. This is the smallest end-to-end SDK verification flow
from the official Python example.
"""

from __future__ import annotations

import os
import sys

from agentbay import AgentBay


def main() -> int:
    api_key = os.getenv("AGENTBAY_API_KEY")
    if not api_key:
        print("Please set AGENTBAY_API_KEY environment variable")
        return 1

    agent_bay = AgentBay(api_key=api_key)
    session_result = agent_bay.create()
    if not session_result.success:
        print(f"Session creation failed: {session_result.error_message}")
        return 1

    session = session_result.session
    print(f"Session created: {session.session_id}")

    agent_bay.delete(session)
    print("Test completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
