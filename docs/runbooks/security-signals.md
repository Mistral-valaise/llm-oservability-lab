# Runbook: PromptInjectionSpike / AuthFailuresSpike

1. Which tenant? `sum by (llm_gateway_tenant) (rate(llm_gateway_guardrail_blocks_total[5m]))`. For auth failures there is no tenant: look at the gateway logs for `unauthorized` and at the ingress logs for the source address.
2. Logs never contain prompts. If you need the content for an investigation, enable `gateway.captureContent` temporarily, and also set `security.dropPromptContent=false` so the collector keeps it. Both are audited changes in Git.
3. Contain: rotate the tenant key (delete the key line in Secret `ai-gateway-keys` and restart the gateway), or block the source at the ingress.
4. Follow up: add the new pattern to `guardrails.py` with a unit test.
