#!/usr/bin/env python3
"""Generate the Grafana dashboard JSON. Run: python3 scripts/gen_dashboard.py

Dashboards as code: panels are declared here and the JSON is committed, so
reviews show a readable diff of queries rather than a wall of generated JSON.
"""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "charts/lab-platform/dashboards/ai-gateway.json"
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
TEMPO = {"type": "tempo", "uid": "tempo"}
RI = "$__rate_interval"
DUR = "gen_ai_client_operation_duration_seconds"
JOB = 'job=~".*ai-gateway"'

_id = 0


def nid() -> int:
    global _id
    _id += 1
    return _id


def row(title: str, y: int) -> dict:
    return {"type": "row", "title": title, "id": nid(), "collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}}


def ts(title, targets, x, y, w=8, h=8, unit="short", desc="", exemplars=False, stack=False):
    return {
        "type": "timeseries", "id": nid(), "title": title, "description": desc, "datasource": PROM,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": {"unit": unit, "custom": {
            "lineWidth": 2, "fillOpacity": 10, "stacking": {"mode": "normal" if stack else "none"}}},
            "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
        "targets": [{"refId": chr(65 + i), "datasource": PROM, "expr": e, "legendFormat": lf,
                     "exemplar": exemplars} for i, (e, lf) in enumerate(targets)],
    }


def stat(title, expr, x, y, unit="short", w=6, h=5, thresholds=None, desc="", decimals=None):
    steps = thresholds or [{"color": "green", "value": None}]
    defaults = {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}}
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "type": "stat", "id": nid(), "title": title, "description": desc, "datasource": PROM,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background", "graphMode": "area"},
        "targets": [{"refId": "A", "datasource": PROM, "expr": expr, "instant": False}],
    }


panels = []
y = 0
panels.append(row("SLO - availability 99 %, latency p95 < 2.56 s", y)); y += 1
panels += [
    stat("Availability (range)",
         f'1 - (sum(increase({DUR}_count{{{JOB},error_type!=""}}[$__range])) '
         f'/ clamp_min(sum(increase({DUR}_count{{{JOB}}}[$__range])), 1))',
         0, y, unit="percentunit", decimals=3,
         thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 0.98},
                     {"color": "green", "value": 0.99}],
         desc="Share of LLM calls without an upstream error. Guardrail and rate-limit rejections are excluded."),
    stat("Error budget remaining (range)",
         f'1 - ((sum(increase({DUR}_count{{{JOB},error_type!=""}}[$__range])) '
         f'/ clamp_min(sum(increase({DUR}_count{{{JOB}}}[$__range])), 1)) / 0.01)',
         6, y, unit="percentunit", decimals=1,
         thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 0.25},
                     {"color": "green", "value": 0.5}]),
    stat("Burn rate (1h)", "slo:sli_error:ratio_rate1h / 0.01", 12, y, decimals=2,
         thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 6},
                     {"color": "red", "value": 14.4}],
         desc="1 = budget lasts exactly the SLO window. 14.4 pages, 6 opens a ticket."),
    stat("Latency SLI (p95 under 2.56 s)",
         f'sum(rate({DUR}_bucket{{{JOB},le="2.56"}}[5m])) / clamp_min(sum(rate({DUR}_count{{{JOB}}}[5m])), 1e-9)',
         18, y, unit="percentunit", decimals=2,
         thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 0.95}]),
]
y += 5

panels.append(row("Golden signals", y)); y += 1
panels += [
    ts("LLM calls per second by model",
       [(f"sum by (gen_ai_request_model) (rate({DUR}_count{{{JOB}}}[{RI}]))", "{{gen_ai_request_model}}")],
       0, y, unit="reqps"),
    ts("Upstream error ratio",
       [(f'sum by (error_type) (rate({DUR}_count{{{JOB},error_type!=""}}[{RI}])) '
         f'/ scalar(sum(rate({DUR}_count{{{JOB}}}[{RI}])))', "{{error_type}}")],
       8, y, unit="percentunit", stack=True),
    ts("Latency p50 / p95 / p99 (click a dot for the trace)",
       [(f"histogram_quantile({q}, sum by (le) (rate({DUR}_bucket{{{JOB}}}[{RI}])))", f"p{int(q * 100)}")
        for q in (0.5, 0.95, 0.99)],
       16, y, unit="s", exemplars=True,
       desc="Exemplars link a latency bucket to a concrete trace in Tempo."),
]
y += 8

panels.append(row("Tokens and cost", y)); y += 1
panels += [
    ts("Tokens per second by type",
       [("sum by (gen_ai_token_type) (rate(gen_ai_client_token_usage_sum[" + RI + "]))", "{{gen_ai_token_type}}")],
       0, y, unit="short", stack=True),
    ts("Spend per tenant (USD per hour)",
       [("sum by (llm_gateway_tenant) (rate(llm_gateway_cost_total[" + RI + "])) * 3600",
         "{{llm_gateway_tenant}}")],
       8, y, unit="currencyUSD"),
    stat("Spend in range (USD)", "sum(increase(llm_gateway_cost_total[$__range]))", 16, y,
         unit="currencyUSD", w=8, h=8, decimals=4),
]
y += 8

panels.append(row("Security signals", y)); y += 1
panels += [
    ts("Guardrail blocks by reason",
       [("sum by (llm_gateway_guardrail_reason) (rate(llm_gateway_guardrail_blocks_total[" + RI + "]))",
         "{{llm_gateway_guardrail_reason}}")],
       0, y, unit="reqps"),
    ts("PII redacted before leaving the gateway",
       [("sum by (llm_gateway_pii_type) (rate(llm_gateway_pii_redactions_total[" + RI + "]))",
         "{{llm_gateway_pii_type}}")],
       8, y, unit="short", stack=True),
    ts("Rate-limit rejections and auth failures",
       [("sum by (llm_gateway_tenant) (rate(llm_gateway_ratelimit_rejections_total[" + RI + "]))",
         "rate limited: {{llm_gateway_tenant}}"),
        ("sum(rate(llm_gateway_auth_failures_total[" + RI + "]))", "auth failures")],
       16, y, unit="reqps"),
]
y += 8

panels.append(row("Logs and traces", y)); y += 1
panels += [
    {
        "type": "logs", "id": nid(), "title": "Gateway logs (trace_id links to Tempo)", "datasource": LOKI,
        "gridPos": {"h": 10, "w": 12, "x": 0, "y": y},
        "options": {"showTime": True, "wrapLogMessage": True, "enableLogDetails": True, "sortOrder": "Descending"},
        "targets": [{"refId": "A", "datasource": LOKI, "expr": '{service_name="ai-gateway"}'}],
    },
    {
        "type": "table", "id": nid(), "title": "Slowest recent LLM calls", "datasource": TEMPO,
        "gridPos": {"h": 10, "w": 12, "x": 12, "y": y},
        "targets": [{"refId": "A", "datasource": TEMPO, "queryType": "traceql", "limit": 20,
                     "query": '{ span.gen_ai.operation.name = "chat" && duration > 1s }'}],
    },
]
y += 10

panels.append(row("Telemetry pipeline health", y)); y += 1
panels += [
    ts("Collector: spans accepted vs failed to export",
       [("sum(rate(otelcol_receiver_accepted_spans_total[" + RI + "]))", "accepted"),
        ("sum(rate(otelcol_receiver_refused_spans_total[" + RI + "]))", "refused"),
        ("sum(rate(otelcol_exporter_send_failed_spans_total[" + RI + "]))", "export failed")],
       0, y, w=12, unit="short"),
    ts("Collector: exporter queue usage",
       [("max by (exporter) (otelcol_exporter_queue_size / otelcol_exporter_queue_capacity)", "{{exporter}}")],
       12, y, w=12, unit="percentunit"),
]

dashboard = {
    "uid": "ai-gateway",
    "title": "AI Gateway - LLM Observability",
    "tags": ["llm", "opentelemetry", "slo", "security"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 1,
    "refresh": "30s",
    "time": {"from": "now-1h", "to": "now"},
    "editable": True,
    "panels": panels,
    "templating": {"list": []},
    "annotations": {"list": []},
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8")
print(f"wrote {OUT.relative_to(OUT.parents[3])} ({len(panels)} panels)")
