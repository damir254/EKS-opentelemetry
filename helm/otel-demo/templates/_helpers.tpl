{{- define "otel-demo.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "otel-demo.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "otel-demo.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end }}
{{- end }}

{{- define "otel-demo.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "otel-demo.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "otel-demo.selectorLabels" -}}
app.kubernetes.io/name: {{ include "otel-demo.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "otel-demo.probes" -}}
{{- range $name := list "startupProbe" "readinessProbe" "livenessProbe" }}
{{- with index $ $name }}
{{ $name }}:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}
{{- end }}
