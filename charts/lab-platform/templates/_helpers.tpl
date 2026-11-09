{{- define "lab.labels" -}}
app.kubernetes.io/part-of: llm-observability-lab
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{- define "lab.sel" -}}
podSelector:
  matchLabels: {{- toYaml . | nindent 4 }}
{{- end }}
