"""OpenTelemetry → Dynatrace OTLP configuration."""

import os
from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

load_dotenv()

_initialized = False


def init_tracing(service_name: str = "agentcore-browser-demo") -> trace.Tracer:
    """Initialize OTel tracing with Dynatrace OTLP export. Returns a tracer."""
    global _initialized
    if _initialized:
        return trace.get_tracer(service_name)

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
    _initialized = True
    return trace.get_tracer(service_name)
