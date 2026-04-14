"""Strands agent with AgentCore Browser — traced with OpenTelemetry → Dynatrace."""

import os
from dotenv import load_dotenv
from opentelemetry import trace
from strands import Agent
from strands_tools.browser import AgentCoreBrowser
from otel_setup import init_tracing

load_dotenv()

REGION = os.environ["AWS_REGION"]
TARGET_URL = "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html"

PROMPT = (
    f"Navigate to {TARGET_URL} and give me a concise summary of "
    "what AgentCore is and its key capabilities."
)


def main():
    tracer = init_tracing("agentcore-browser-agent")

    with tracer.start_as_current_span("browser-agent-session") as span:
        span.set_attribute("agent.prompt", PROMPT)
        span.set_attribute("agent.target_url", TARGET_URL)
        span.set_attribute("aws.region", REGION)

        # Initialize AgentCore Browser tool
        browser_tool = AgentCoreBrowser(region=REGION)
        agent = Agent(tools=[browser_tool.browser])

        with tracer.start_as_current_span("agent-invoke"):
            response = agent(PROMPT)

        result = response.message["content"][0]["text"]
        span.set_attribute("agent.response_length", len(result))
        span.add_event("agent-response", {"response_preview": result[:500]})

        print("\n--- Agent Response ---")
        print(result)

    # Flush traces
    trace.get_tracer_provider().force_flush()
    print("\n✅ Traces exported to Dynatrace")


if __name__ == "__main__":
    main()
