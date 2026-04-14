# Observing AI Browser Agents with AgentCore Browser and Dynatrace

AI agents that browse the web are becoming a real thing. They navigate pages, fill forms, click buttons, extract content, and make decisions — all autonomously. But here's the problem: without observability, you have no idea what they're actually doing.

When your agent takes 45 seconds to complete a task, was it waiting on a page load? Did the LLM take too long to respond? Did a click land on the wrong element? You can't debug what you can't see.

In this post, we'll build an AI browser agent using [Amazon Bedrock AgentCore Browser](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool.html) and instrument it with [OpenTelemetry](https://opentelemetry.io/) traces exported to [Dynatrace](https://www.dynatrace.com/). We'll also demo the brand-new [OS Actions API](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-invoke.html) — mouse clicks, keyboard shortcuts, and full-desktop screenshots at the OS level.

The full code is on GitHub: [jasonmimick-aws/agentcore-browser-dynatrace](https://github.com/jasonmimick-aws/agentcore-browser-dynatrace)

---

## What is AgentCore Browser?

AgentCore Browser is a managed Chrome browser running in AWS. Your agent connects to it and controls it — no local browser needed, no infrastructure to manage. Sessions are isolated, ephemeral, and auto-terminate.

There are two ways to interact with it:

1. **CDP (Chrome DevTools Protocol)** over WebSocket — standard browser automation. Navigate pages, click DOM elements, fill forms. Libraries like Playwright and Strands connect this way.

2. **InvokeBrowser REST API** (new!) — OS-level actions. Mouse clicks at screen coordinates, keyboard shortcuts, full-desktop screenshots. This handles things CDP can't: native OS dialogs, print prompts, right-click menus, JavaScript alerts.

## Why Dynatrace?

AI browser agents are distributed systems. An agent decision triggers an LLM call, which triggers a browser command, which triggers a page load, which triggers content extraction, which triggers another LLM call. That's a lot of moving parts.

Dynatrace natively supports [OpenTelemetry trace ingestion via OTLP](https://docs.dynatrace.com/docs/extend-dynatrace/opentelemetry). By wrapping each step in an OTel span and exporting to Dynatrace, you get:

- **Trace timelines** — see the full agent session from prompt to response
- **Span attributes** — what URL was visited, what was clicked, what the agent decided
- **Error correlation** — which browser action failed and what was on screen when it happened
- **Performance insights** — where are the bottlenecks across agent runs

---

## The Architecture

<p align="center"><img src="images/architecture-diagram.png" width="60%"></p>

The Python agent creates OTel spans for each action. Spans are exported via OTLP/HTTP to Dynatrace. AgentCore Browser runs in AWS — your agent connects via CDP for standard automation and via REST for OS-level actions.

---

## Part 1: Setting Up OTel → Dynatrace

The foundation is a small module that configures the OpenTelemetry `TracerProvider` to export spans to Dynatrace:

```python
# src/otel_setup.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

def init_tracing(service_name="agentcore-browser-demo"):
    endpoint = os.environ["DT_OTLP_ENDPOINT"] + "/v1/traces"
    token = os.environ["DT_API_TOKEN"]

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=endpoint,
        headers={"Authorization": f"Api-Token {token}"},
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)
```

You need a Dynatrace API token with the `opentelemetryTrace.ingest` scope. The OTLP endpoint is `https://<your-tenant>.live.dynatrace.com/api/v2/otlp`.

---

## Part 2: The Strands Browser Agent

[Strands Agents](https://github.com/strands-agents/strands-agents) is an open-source SDK for building AI agents. It has a built-in AgentCore Browser tool and — crucially — its own OpenTelemetry instrumentation. That means when you wrap the agent call in your own span, you get a rich trace with LLM calls, tool executions, and event loop cycles all nested inside.

```python
# src/agent_browser.py
from strands import Agent
from strands_tools.browser import AgentCoreBrowser
from otel_setup import init_tracing

tracer = init_tracing("agentcore-browser-agent")

with tracer.start_as_current_span("browser-agent-session") as span:
    span.set_attribute("agent.target_url", TARGET_URL)

    browser_tool = AgentCoreBrowser(region="us-east-1")
    agent = Agent(tools=[browser_tool.browser])

    with tracer.start_as_current_span("agent-invoke"):
        response = agent("Navigate to the AgentCore docs and summarize the key capabilities.")

    span.set_attribute("agent.response_length", len(response.message["content"][0]["text"]))
```

When you run this, the agent creates a browser session, navigates to the page, reads the content, and summarizes it with Claude. The entire flow is captured as a trace.

### What it looks like in Dynatrace

![Strands Agent Trace in Dynatrace](https://raw.githubusercontent.com/jasonmimick-aws/agentcore-browser-dynatrace/main/docs/images/dynatrace-strands-agent-trace.png)

The trace shows 17 spans over 44 seconds. You can see the repeating pattern: `chat` (LLM call) → `execute_tool browser` → `execute_event_loop_cycle` — the agent reasoning loop. Span attributes show the model (`us.anthropic.claude-sonnet-...`), token timing, and the Strands agent system.

---

## Part 3: OS Actions — The New Stuff

The [InvokeBrowser API](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-invoke.html) adds OS-level control that goes beyond what CDP can do:

| Action | What it does |
|--------|-------------|
| `mouseClick` | Click at OS coordinates (LEFT/RIGHT/MIDDLE) |
| `mouseMove` | Move cursor to coordinates |
| `mouseDrag` | Drag from start to end position |
| `mouseScroll` | Scroll at position |
| `keyType` | Type a string of text |
| `keyPress` | Press a key N times |
| `keyShortcut` | Key combination (e.g., `["ctrl", "a"]`) |
| `screenshot` | Full desktop screenshot (PNG) |

Our demo navigates to a page via Playwright CDP, then uses OS Actions for screenshots, clicks, and keyboard shortcuts — each wrapped in its own OTel span:

```python
# Take an OS-level screenshot
with tracer.start_as_current_span("os-screenshot") as span:
    resp = dp_client.invoke_browser(
        browserIdentifier="aws.browser.v1",
        sessionId=session_id,
        action={"screenshot": {"format": "PNG"}}
    )
    result = resp["result"]["screenshot"]
    span.set_attribute("screenshot.status", result["status"])
    # boto3 returns raw bytes — write directly
    with open("screenshot.png", "wb") as f:
        f.write(result["data"])
```

One gotcha we hit: the boto3 SDK auto-decodes the base64 response, so `result["data"]` is already raw PNG bytes. Don't double-decode with `base64.b64decode()` or you'll get a corrupted file.

### The screenshot

Here's an actual screenshot captured by AgentCore Browser's OS-level `screenshot` action — it navigated to this project's GitHub repo:

![AgentCore Browser Screenshot](docs/images/agentcore-browser-screenshot.png)

This is a full desktop capture, not just the browser viewport. That's the key difference from CDP screenshots — you see everything the OS sees, including native dialogs, notifications, and multi-window layouts.

### The trace in Dynatrace

![OS Actions Trace in Dynatrace](https://raw.githubusercontent.com/jasonmimick-aws/agentcore-browser-dynatrace/main/docs/images/dynatrace-os-actions-trace.png)

Each OS action is a separate span with its own attributes:

```
os-actions-session (parent)
  ├── cdp-navigate          → page.title
  ├── os-screenshot         → screenshot.status, screenshot.path
  ├── os-mouse-click        → click.status, click.x, click.y
  ├── os-key-shortcut       → shortcut.status, shortcut.keys
  └── os-screenshot-final   → screenshot.status, screenshot.path
```

---

## Going Further

This demo covers the trace pillar of observability. AgentCore Browser gives you more:

- **CloudWatch metrics** — session counts, duration, error rates, CPU/memory/network utilization. Connect these to Dynatrace via the [AWS CloudWatch integration](https://docs.dynatrace.com/docs/setup-and-configuration/setup-on-cloud-platforms/amazon-web-services) for a unified view.

- **Session recording** — enable recording to S3 and get DOM replay, console logs, CDP events, and network events. Forward these to Dynatrace for log correlation with your traces.

- **Live View** — watch your agent browse in real-time through the [AgentCore Browser Console](https://console.aws.amazon.com/bedrock-agentcore/builtInTools). Great for debugging during development.

---

## Try It Yourself

```bash
git clone https://github.com/jasonmimick-aws/agentcore-browser-dynatrace
cd agentcore-browser-dynatrace
uv pip install -e .
playwright install chromium
cp .env.example .env  # fill in your AWS region and Dynatrace token
cd src && python agent_browser.py
```

You'll need:
- AWS credentials with AgentCore Browser permissions (see `infra/iam-policy.json`)
- Claude Sonnet model access enabled in Amazon Bedrock
- A Dynatrace environment with an API token that has `opentelemetryTrace.ingest` scope

---

## Wrapping Up

AI agents browsing the web are distributed systems. They deserve the same observability you'd give any production service. By combining AgentCore Browser's managed Chrome environment with OpenTelemetry traces exported to Dynatrace, you get full visibility into what your agent is doing, how long it takes, and where things go wrong.

The new OS Actions API makes this even more powerful — you can now capture exactly what the agent sees (full-desktop screenshots) and correlate it with trace spans showing exactly what the agent did.

**Links:**
- [GitHub repo](https://github.com/jasonmimick-aws/agentcore-browser-dynatrace)
- [AgentCore Browser docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool.html)
- [InvokeBrowser (OS Actions) API](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-invoke.html)
- [Strands Agents](https://github.com/strands-agents/strands-agents)
- [Dynatrace OpenTelemetry integration](https://docs.dynatrace.com/docs/extend-dynatrace/opentelemetry)
