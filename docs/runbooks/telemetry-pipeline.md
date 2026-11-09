# Runbook: TelemetryExportFailing

**Meaning:** the collector cannot deliver to Tempo, Prometheus or Loki. Dashboards will show gaps.

1. `kubectl logs deploy/otel-collector` - look for the failing exporter.
2. Backend healthy? `kubectl get pods`; for Loki and Tempo check PVC usage (`kubectl get pvc`).
3. NetworkPolicy change? `kubectl get netpol otel-collector -o yaml` must allow egress to the backend port.
4. Queue panel *Collector: exporter queue usage* near 100 % means data loss is imminent; scale the backend or reduce load (`make up DISABLE=loadgen`).
5. Clients failing with TLS errors after a reinstall: the certificates were regenerated; restart gateway and loadgen so they mount the new client certificate.
