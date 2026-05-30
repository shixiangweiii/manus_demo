"""Control an AgentBay cloud browser with Playwright over CDP.

Requires AGENTBAY_API_KEY and an AgentBay entitlement that supports
`get_endpoint_url`. The script creates a `browser_latest` session, connects
Playwright to the remote browser, opens aliyun.com, prints the page title, and
then cleans up the session.
"""

from __future__ import annotations

import os
import sys
import time

from agentbay import AgentBay, BrowserOption, CreateSessionParams
from playwright.sync_api import sync_playwright


def main() -> int:
    api_key = os.getenv("AGENTBAY_API_KEY")
    if not api_key:
        print("Please set AGENTBAY_API_KEY environment variable")
        return 1

    agent_bay = AgentBay(api_key=api_key)
    session_result = agent_bay.create(CreateSessionParams(image_id="browser_latest"))
    if not session_result.success:
        print(f"Session creation failed: {session_result.error_message}")
        return 1

    session = session_result.session
    try:
        initialized = session.browser.initialize(BrowserOption())
        if not initialized:
            print("Browser initialization failed")
            return 1

        endpoint_url = session.browser.get_endpoint_url()
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint_url)
            try:
                context = browser.contexts[0]
                page = context.new_page()
                page.goto("https://www.aliyun.com", wait_until="domcontentloaded")
                print("Title:", page.title())
                time.sleep(5)
            finally:
                browser.close()

        return 0
    finally:
        agent_bay.delete(session)


if __name__ == "__main__":
    sys.exit(main())
