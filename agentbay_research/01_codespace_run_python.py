"""Run Python code in an AgentBay CodeSpace session.

Requires AGENTBAY_API_KEY. The example creates a `code_latest` session, runs a
small Python snippet remotely, prints the result, and cleans up the session.
"""

from __future__ import annotations

import os
import sys
from textwrap import dedent

from agentbay import AgentBay, CreateSessionParams


CODE = dedent(
    """
    import math


    def factorial(n):
        return math.factorial(n)


    def fibonacci(n):
        if n <= 1:
            return n
        return fibonacci(n - 1) + fibonacci(n - 2)


    print(f"Factorial of 10: {factorial(10)}")
    print(f"Fibonacci of 10: {fibonacci(10)}")
    print(f"Squares: {[x ** 2 for x in range(1, 11)]}")
    """
).strip()


def main() -> int:
    api_key = os.getenv("AGENTBAY_API_KEY")
    if not api_key:
        print("Please set AGENTBAY_API_KEY environment variable")
        return 1

    agent_bay = AgentBay(api_key=api_key)
    session_result = agent_bay.create(CreateSessionParams(image_id="code_latest"))
    if not session_result.success:
        print(f"Session creation failed: {session_result.error_message}")
        return 1

    session = session_result.session
    try:
        run_result = session.code.run_code(CODE, "python")
        if not run_result.success:
            print(f"Code execution failed: {run_result.error_message}")
            return 1

        print("Output:")
        print(run_result.result)
        return 0
    finally:
        agent_bay.delete(session)


if __name__ == "__main__":
    sys.exit(main())
