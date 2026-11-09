{{- define "aigw.labels" -}}
app.kubernetes.io/part-of: llm-observability-lab
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{- define "aigw.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}
{{- end }}

{{/* Pod security: restricted profile. On OpenShift the SCC assigns UID/GID. */}}
{{- define "aigw.podSecurityContext" -}}
runAsNonRoot: true
{{- if not .Values.openshift }}
runAsUser: 10001
runAsGroup: 0
fsGroup: 10001
{{- end }}
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "aigw.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end }}

{{/* Shared OTel env: mTLS to the collector, k8s resource attributes via the downward API. */}}
{{- define "aigw.otelEnv" -}}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .Values.otel.endpoint | quote }}
{{- if .Values.otel.tlsSecret }}
- name: OTEL_EXPORTER_OTLP_CERTIFICATE
  value: /etc/otel-tls/ca.crt
- name: OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE
  value: /etc/otel-tls/tls.crt
- name: OTEL_EXPORTER_OTLP_CLIENT_KEY
  value: /etc/otel-tls/tls.key
{{- end }}
- name: K8S_POD_NAME
  valueFrom: {fieldRef: {fieldPath: metadata.name}}
- name: K8S_NAMESPACE
  valueFrom: {fieldRef: {fieldPath: metadata.namespace}}
- name: OTEL_RESOURCE_ATTRIBUTES
  value: "k8s.pod.name=$(K8S_POD_NAME),k8s.namespace.name=$(K8S_NAMESPACE),deployment.environment.name={{ .Values.otel.environment }}"
- name: OTEL_SEMCONV_STABILITY_OPT_IN
  value: http
{{- end }}

{{- define "aigw.otelVolume" -}}
{{- if .Values.otel.tlsSecret }}
- name: otel-tls
  secret:
    secretName: {{ .Values.otel.tlsSecret }}
    defaultMode: 0440
{{- end }}
{{- end }}

{{- define "aigw.otelVolumeMount" -}}
{{- if .Values.otel.tlsSecret }}
- name: otel-tls
  mountPath: /etc/otel-tls
  readOnly: true
{{- end }}
{{- end }}
