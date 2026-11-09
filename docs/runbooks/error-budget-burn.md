# Runbook: GatewayErrorBudgetFastBurn / SlowBurn

**Meaning:** the share of failed upstream LLM calls is 14.4x (page) or 6x (ticket) higher than the 99 % SLO allows.

## Triage (5 minutes)

1. Dashboard *AI Gateway*, row *Golden signals*: which `error_type` dominates? `rate_limited` points at the provider quota, `unavailable` or `timeout` at the provider itself.
2. Is it one model? Split the error ratio by `gen_ai_request_model`.
3. Open an exemplar on the latency panel or search Tempo: `{ span.gen_ai.operation.name = "chat" && status = error }`. The span shows `error.type` and the provider address.
4. From the trace, jump to the logs (`trace_id`) to see the gateway's view.

## Mitigate

- Provider rate limit: lower tenant rate limits (`gateway.rateLimit`) or switch noisy tenants off.
- Provider outage: fail over to another model in `allowedModels`, communicate degradation.
- Demo: `make calm` resets the chaos settings.

## Verify

`slo:sli_error:ratio_rate5m` falls below 0.01 and the alert resolves.
