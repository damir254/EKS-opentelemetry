{{/* Render the resources for a values-defined demo service. */}}

{{- define "otel-demo.serviceName" -}}
{{- .name | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "otel-demo.serviceAccountName" -}}
{{- if .service.serviceAccount.create -}}
{{- include "otel-demo.serviceName" . -}}
{{- else -}}
{{- .service.serviceAccount.name | default "" -}}
{{- end -}}
{{- end }}

{{- define "otel-demo.serviceSelectorLabels" -}}
{{- if .service.selectorLabels.includeCommon }}
{{ include "otel-demo.selectorLabels" .root }}
{{- end }}
app.kubernetes.io/component: {{ .name }}
{{- end }}

{{- define "otel-demo.servicePodLabels" -}}
{{ include "otel-demo.selectorLabels" .root }}
app.kubernetes.io/component: {{ .name }}
{{- end }}

{{- define "otel-demo.serviceAccount" -}}
{{- if .service.serviceAccount.create }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "otel-demo.serviceName" . }}
  labels:
    {{- include "otel-demo.labels" .root | nindent 4 }}
    app.kubernetes.io/component: {{ .name }}
automountServiceAccountToken: {{ .service.serviceAccount.automount }}
{{- end }}
{{- end }}

{{- define "otel-demo.serviceConfigMaps" -}}
{{- range .service.mountedConfigMaps }}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ .name }}
  labels:
    {{- include "otel-demo.labels" $.root | nindent 4 }}
    app.kubernetes.io/component: {{ $.name }}
data:
  {{- toYaml .data | nindent 2 }}
---
{{- end }}
{{- end }}

{{- define "otel-demo.workloadType" -}}
{{- if (.service.rollout | default dict).enabled }}
apiVersion: argoproj.io/v1alpha1
kind: Rollout
{{- else }}
apiVersion: apps/v1
kind: Deployment
{{- end }}
{{- end }}

{{- define "otel-demo.serviceWorkload" -}}
{{- $rollout := .service.rollout | default dict }}
{{- $istio := .service.istio | default dict }}
{{ include "otel-demo.workloadType" . }}
metadata:
  name: {{ include "otel-demo.serviceName" . }}
  labels:
    {{- include "otel-demo.labels" .root | nindent 4 }}
    app.kubernetes.io/component: {{ .name }}
spec:
  replicas: {{ .service.replicaCount }}
  {{- if $rollout.enabled }}
  revisionHistoryLimit: 3
  progressDeadlineSeconds: 600
  progressDeadlineAbort: true
  strategy:
    {{- toYaml $rollout.strategy | nindent 4 }}
  {{- end }}
  selector:
    matchLabels:
      {{- include "otel-demo.serviceSelectorLabels" . | nindent 6 }}
  template:
    metadata:
      {{- with $istio.excludeOutboundPorts }}
      annotations:
        traffic.sidecar.istio.io/excludeOutboundPorts: {{ . | quote }}
      {{- end }}
      labels:
        {{- include "otel-demo.servicePodLabels" . | nindent 8 }}
        {{- if $istio.enabled }}
        sidecar.istio.io/inject: "true"
        {{- end }}
    spec:
      enableServiceLinks: false

      {{- $topologySpread := .service.topologySpread | default (dict) }}
      {{- if and $topologySpread.enabled (or $topologySpread.zone $topologySpread.hostname) }}
      topologySpreadConstraints:
        {{- if $topologySpread.zone }}
        - maxSkew: 1
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              {{- include "otel-demo.servicePodLabels" . | nindent 14 }}
        {{- end }}
        {{- if $topologySpread.hostname }}
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              {{- include "otel-demo.servicePodLabels" . | nindent 14 }}
        {{- end }}
      {{- end }}

      {{- $serviceAccountName := include "otel-demo.serviceAccountName" . }}
      {{- if $serviceAccountName }}
      serviceAccountName: {{ $serviceAccountName }}
      {{- end }}

      {{- if hasKey .service "podAutomountServiceAccountToken" }}
      automountServiceAccountToken: {{ .service.podAutomountServiceAccountToken }}
      {{- end }}

      {{- with .service.podSecurityContext }}
      securityContext:
        {{- toYaml . | nindent 8 }}
      {{- end }}

      {{- with .service.initContainers }}
      initContainers:
        {{- toYaml . | nindent 8 }}
      {{- end }}

      containers:
        - name: {{ .name }}
          image: "{{ .service.image.repository }}:{{ .service.image.tag }}"
          imagePullPolicy: {{ .service.image.pullPolicy }}

          ports:
            - name: {{ .service.service.portName }}
              containerPort: {{ .service.containerPort }}
              protocol: {{ .service.service.protocol }}

          {{- $defaultEnv := deepCopy (default (dict) .root.Values.defaultEnv) }}
          {{- $serviceEnv := default (dict) .service.env }}
          {{- $mergedEnv := mergeOverwrite $defaultEnv $serviceEnv }}
          {{- if $rollout.enabled }}
          {{/* Merge existing resource attributes, updating only the version/hash. */}}
          {{- $attributes := list }}
          {{- range $envName := list "OTEL_RESOURCE_ATTRIBUTES" "OTEL_RESOURCE_ATTRIBUTES_EXTRA" }}
          {{- $env := get $mergedEnv $envName | default dict }}
          {{- $value := $env }}
          {{- if kindIs "map" $env }}
          {{- if hasKey $env "valueFrom" }}
          {{- fail (printf "%s: rollout resource attributes require a literal %s value" $.name $envName) }}
          {{- end }}
          {{- $value = $env.value | default "" }}
          {{- end }}
          {{- range splitList "," $value }}
          {{- $attribute := trim . }}
          {{- if and $attribute (not (regexMatch "^(service\\.version|rollout\\.pod_template_hash)=" $attribute)) }}
          {{- $attributes = append $attributes $attribute }}
          {{- end }}
          {{- end }}
          {{- end }}
          {{- $attributes = append $attributes "rollout.pod_template_hash=$(ROLLOUT_POD_TEMPLATE_HASH)" }}
          {{- $attributes = append $attributes (printf "service.version=%s" .service.image.tag) }}
          {{- $_ := set $mergedEnv "OTEL_RESOURCE_ATTRIBUTES" (dict "value" (join "," $attributes)) }}
          {{- end }}

          env:
            {{- if $rollout.enabled }}
            # Must precede OTEL_RESOURCE_ATTRIBUTES for Kubernetes env expansion.
            - name: ROLLOUT_POD_TEMPLATE_HASH
              valueFrom:
                fieldRef:
                  fieldPath: metadata.labels['rollouts-pod-template-hash']
            {{- end }}
            {{- range $key, $env := $mergedEnv }}
            - name: {{ $key }}
              {{- if kindIs "map" $env }}
              {{- if hasKey $env "valueFrom" }}
              valueFrom:
                {{- toYaml $env.valueFrom | nindent 16 }}
              {{- else if hasKey $env "value" }}
              value: {{ $env.value | quote }}
              {{- end }}
              {{- else }}
              value: {{ $env | quote }}
              {{- end }}
            {{- end }}

          resources:
            {{- toYaml .service.resources | nindent 12 }}

          securityContext:
            {{- toYaml .service.securityContext | nindent 12 }}

          {{- if or .service.mountedEmptyDirs .service.mountedConfigMaps }}
          volumeMounts:
            {{- range .service.mountedEmptyDirs }}
            - name: {{ .name }}
              mountPath: {{ .mountPath }}
            {{- end }}

            {{- range .service.mountedConfigMaps }}
            - name: {{ .name }}
              mountPath: {{ .mountPath }}
              {{- with .subPath }}
              subPath: {{ . }}
              {{- end }}
            {{- end }}
          {{- end }}

      {{- if or .service.mountedEmptyDirs .service.mountedConfigMaps }}
      volumes:
        {{- range .service.mountedEmptyDirs }}
        - name: {{ .name }}
          emptyDir: {}
        {{- end }}

        {{- range .service.mountedConfigMaps }}
        - name: {{ .name }}
          configMap:
            name: {{ .name }}
        {{- end }}
      {{- end }}
{{- end }}

{{- define "otel-demo.serviceService" -}}
apiVersion: v1
kind: Service
metadata:
  name: {{ include "otel-demo.serviceName" . }}
  labels:
    {{- include "otel-demo.labels" .root | nindent 4 }}
    app.kubernetes.io/component: {{ .name }}
spec:
  type: {{ .service.service.type }}
  selector:
    {{- include "otel-demo.serviceSelectorLabels" . | nindent 4 }}
  ports:
    - name: {{ .service.service.portName }}
      port: {{ .service.service.port }}
      targetPort: {{ .service.service.targetPort }}
      protocol: {{ .service.service.protocol }}
{{- end }}

{{- define "otel-demo.serviceHPA" -}}
{{- if and .service.hpa .service.hpa.enabled }}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ include "otel-demo.serviceName" . }}
  labels:
    {{- include "otel-demo.labels" .root | nindent 4 }}
    app.kubernetes.io/component: {{ .name }}
spec:
  scaleTargetRef:
    {{- include "otel-demo.workloadType" . | nindent 4 }}
    name: {{ include "otel-demo.serviceName" . }}
  minReplicas: {{ .service.hpa.minReplicas }}
  maxReplicas: {{ .service.hpa.maxReplicas }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ .service.hpa.targetCPUUtilizationPercentage }}
{{- end }}
{{- end }}

{{- define "otel-demo.servicePDB" -}}
{{- if and .service.pdb .service.pdb.enabled }}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "otel-demo.serviceName" . }}
  labels:
    {{- include "otel-demo.labels" .root | nindent 4 }}
    app.kubernetes.io/component: {{ .name }}
spec:
  minAvailable: {{ .service.pdb.minAvailable }}
  selector:
    matchLabels:
      {{- include "otel-demo.serviceSelectorLabels" . | nindent 6 }}
{{- end }}
{{- end }}

{{- define "otel-demo.serviceNetworkPolicy" -}}
{{- if and .service.networkPolicy .service.networkPolicy.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ include "otel-demo.serviceName" . }}
  labels:
    {{- include "otel-demo.labels" .root | nindent 4 }}
    app.kubernetes.io/component: {{ .name }}
spec:
  podSelector:
    matchLabels:
      {{- include "otel-demo.serviceSelectorLabels" . | nindent 6 }}

  policyTypes:
    {{- if .service.networkPolicy.ingress }}
    - Ingress
    {{- end }}
    {{- if .service.networkPolicy.egress }}
    - Egress
    {{- end }}

  {{- with .service.networkPolicy.ingress }}
  ingress:
    {{- toYaml . | nindent 4 }}
  {{- end }}

  {{- with .service.networkPolicy.egress }}
  egress:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end }}
{{- end }}
