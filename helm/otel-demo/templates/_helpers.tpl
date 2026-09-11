{{/*
Expand the name of the chart.
*/}}
{{- define "otel-demo.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{/*
Create a fully qualified application name.
*/}}
{{- define "otel-demo.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "otel-demo.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "otel-demo.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "otel-demo.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "otel-demo.selectorLabels" -}}
app.kubernetes.io/name: {{ include "otel-demo.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}