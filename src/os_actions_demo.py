"""OS Actions demo — InvokeBrowser API (screenshot, click, keyboard) with OTel tracing."""

import os
import base64
from datetime import datetime
from dotenv import load_dotenv
from opentelemetry import trace
import boto3
from bedrock_agentcore.tools.browser_client import browser_session
from otel_setup import init_tracing

load_dotenv()

REGION = os.environ["AWS_REGION"]
BROWSER_ID = "aws.browser.v1"
SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "screenshots")


def invoke_action(client, session_id: str, action: dict) -> dict:
    """Call InvokeBrowser with a single OS action."""
    dp = boto3.client("bedrock-agentcore", region_name=REGION)
    return dp.invoke_browser(
        browserIdentifier=BROWSER_ID,
        sessionId=session_id,
        action=action,
    )


def save_screenshot(data_b64: str, label: str) -> str:
    """Decode and save a base64 screenshot, return the file path."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SCREENSHOT_DIR, f"{label}_{ts}.png")
    with open(path, "wb") as f:
        f.write(base64.b64decode(data_b64))
    return path


def main():
    tracer = init_tracing("agentcore-os-actions")

    with tracer.start_as_current_span("os-actions-session") as root:
        root.set_attribute("aws.region", REGION)

        with browser_session(REGION) as client:
            session_id = client.session_id
            root.set_attribute("browser.session_id", session_id)
            print(f"🌐 Browser session: {session_id}")

            # --- Navigate via Playwright CDP first ---
            import asyncio, nest_asyncio
            from playwright.async_api import async_playwright

            nest_asyncio.apply()

            async def navigate():
                ws_url, headers = client.generate_ws_headers()
                async with async_playwright() as pw:
                    browser = await pw.chromium.connect_over_cdp(ws_url, headers=headers)
                    page = browser.contexts[0].pages[0]
                    await page.goto("https://aws.amazon.com/bedrock/agentcore/")
                    await asyncio.sleep(3)
                    title = await page.title()
                    return title

            with tracer.start_as_current_span("cdp-navigate") as nav_span:
                title = asyncio.get_event_loop().run_until_complete(navigate())
                nav_span.set_attribute("page.title", title)
                print(f"📄 Page: {title}")

            # --- OS Action: Screenshot ---
            with tracer.start_as_current_span("os-screenshot") as ss_span:
                resp = invoke_action(client, session_id, {"screenshot": {"format": "PNG"}})
                result = resp["result"]["screenshot"]
                ss_span.set_attribute("screenshot.status", result["status"])
                if result["status"] == "SUCCESS":
                    path = save_screenshot(result["data"], "initial")
                    ss_span.set_attribute("screenshot.path", path)
                    print(f"📸 Screenshot saved: {path}")

            # --- OS Action: Click (scroll down area) ---
            with tracer.start_as_current_span("os-mouse-click") as click_span:
                resp = invoke_action(client, session_id, {
                    "mouseClick": {"x": 728, "y": 400, "button": "LEFT", "clickCount": 1}
                })
                status = resp["result"]["mouseClick"]["status"]
                click_span.set_attribute("click.status", status)
                click_span.set_attribute("click.x", 728)
                click_span.set_attribute("click.y", 400)
                print(f"🖱️  Click: {status}")

            # --- OS Action: Keyboard shortcut (Ctrl+A select all) ---
            with tracer.start_as_current_span("os-key-shortcut") as key_span:
                resp = invoke_action(client, session_id, {
                    "keyShortcut": {"keys": ["ctrl", "a"]}
                })
                status = resp["result"]["keyShortcut"]["status"]
                key_span.set_attribute("shortcut.status", status)
                key_span.set_attribute("shortcut.keys", "ctrl+a")
                print(f"⌨️  Shortcut ctrl+a: {status}")

            # --- OS Action: Final screenshot ---
            with tracer.start_as_current_span("os-screenshot-final") as ss2:
                resp = invoke_action(client, session_id, {"screenshot": {"format": "PNG"}})
                result = resp["result"]["screenshot"]
                ss2.set_attribute("screenshot.status", result["status"])
                if result["status"] == "SUCCESS":
                    path = save_screenshot(result["data"], "final")
                    ss2.set_attribute("screenshot.path", path)
                    print(f"📸 Final screenshot: {path}")

    trace.get_tracer_provider().force_flush()
    print("\n✅ Traces exported to Dynatrace")


if __name__ == "__main__":
    main()
