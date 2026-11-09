# Runbook: GatewayLatencyHigh

**Meaning:** p95 of LLM calls above 2.56 s for 10 minutes.

1. Latency panel: click an exemplar dot near p95/p99 to open a slow trace.
2. In the trace: is the time in the `chat <model>` client span (provider) or before it (gateway)? Check `gen_ai.usage.output_tokens`; long answers are slow answers.
3. Split by `gen_ai_request_model`: large models are expected to be slower; consider a separate SLO per model class.
4. Mitigate: lower `max_tokens`, route to a smaller model, or raise the provider timeout only if the SLO allows it.
