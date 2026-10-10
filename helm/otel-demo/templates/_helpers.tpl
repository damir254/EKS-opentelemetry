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

{{- define "otel-demo.podResourceAttributes" -}}
service.instance.id: "$(K8S_POD_UID)"
k8s.pod.uid: "$(K8S_POD_UID)"
k8s.pod.name: "$(K8S_POD_NAME)"
k8s.namespace.name: "$(K8S_NAMESPACE_NAME)"
k8s.node.name: "$(K8S_NODE_NAME)"
{{- end }}

{{- define "otel-demo.podIdentityEnv" -}}
{{- range $name := list "K8S_POD_UID" "K8S_POD_NAME" "K8S_NAMESPACE_NAME" "K8S_NODE_NAME" }}
- name: {{ $name }}
  valueFrom:
    {{- toYaml (index $.Values.defaultEnv $name).valueFrom | nindent 4 }}
{{- end }}
{{- end }}

{{- define "otel-demo.podResourceAttributesValue" -}}
{{- $attributes := list }}
{{- range $key, $value := (include "otel-demo.podResourceAttributes" . | fromYaml) }}
{{- $attributes = append $attributes (printf "%s=%s" $key $value) }}
{{- end }}
{{- join "," $attributes }}
{{- end }}
