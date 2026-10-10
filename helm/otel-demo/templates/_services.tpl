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
{{- $data := deepCopy (.data | default dict) }}
{{- $templateData := .templateData | default false }}
{{- range $key, $path := .dataFiles }}
{{- if hasKey $data $key }}
{{- fail (printf "%s: ConfigMap key %s is defined in both data and dataFiles" $.name $key) }}
{{- end }}
{{- $content := required (printf "%s: ConfigMap file %s is missing or empty" $.name $path) ($.root.Files.Get $path) }}
{{- if $templateData }}
{{- $content = tpl $content $.root }}
{{- end }}
{{- $_ := set $data $key $content }}
{{- end }}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ .name }}
  labels:
    {{- include "otel-demo.labels" $.root | nindent 4 }}
    app.kubernetes.io/component: {{ $.name }}
data:
  {{- toYaml $data | nindent 2 }}
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
      {{- $podAnnotations := deepCopy (.service.podAnnotations | default dict) }}
      {{- if .service.mountedConfigMaps }}
      {{- $_ := set $podAnnotations "checksum/config" (include "otel-demo.serviceConfigMaps" . | sha256sum) }}
      {{- end }}
      {{- with $istio.excludeOutboundPorts }}
      {{- $_ := set $podAnnotations "traffic.sidecar.istio.io/excludeOutboundPorts" . }}
      {{- end }}
      {{- with $podAnnotations }}
      annotations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      labels:
        {{- include "otel-demo.servicePodLabels" . | nindent 8 }}
        {{- if $istio.enabled }}
        sidecar.istio.io/inject: "true"
        {{- end }}
    spec:
      enableServiceLinks: false
      {{- with .service.terminationGracePeriodSeconds }}
      terminationGracePeriodSeconds: {{ . }}
      {{- end }}

      {{- $topologySpread := .service.topologySpread | default (dict) }}
      {{- if and $topologySpread.enabled (or $topologySpread.zone $topologySpread.hostname) }}
      topologySpreadConstraints:
        {{- if $topologySpread.zone }}
        - maxSkew: 1
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: {{ $topologySpread.zoneWhenUnsatisfiable | default "ScheduleAnyway" }}
          nodeTaintsPolicy: Honor
          labelSelector:
            matchLabels:
              {{- include "otel-demo.servicePodLabels" . | nindent 14 }}
        {{- end }}
        {{- if $topologySpread.hostname }}
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          nodeTaintsPolicy: Honor
          {{- if ge (int .service.replicaCount) 2 }}
          # Require two eligible nodes even when Auto Mode starts from one.
          minDomains: 2
          {{- end }}
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

          {{- include "otel-demo.probes" .service | nindent 10 }}

          {{- $defaultEnv := deepCopy (default (dict) .root.Values.defaultEnv) }}
          {{- $serviceEnv := default (dict) .service.env }}
          {{- $mergedEnv := mergeOverwrite $defaultEnv $serviceEnv }}
          {{/* Merge service attributes, then enforce a distinct identity per pod. */}}
          {{- $resourceAttributes := dict }}
          {{- range $envName := list "OTEL_RESOURCE_ATTRIBUTES" "OTEL_RESOURCE_ATTRIBUTES_EXTRA" }}
          {{- $env := get $mergedEnv $envName | default dict }}
          {{- $value := $env }}
          {{- if kindIs "map" $env }}
          {{- if hasKey $env "valueFrom" }}
          {{- fail (printf "%s: resource attributes require a literal %s value" $.name $envName) }}
          {{- end }}
          {{- $value = $env.value | default "" }}
          {{- end }}
          {{- range splitList "," $value }}
          {{- $attribute := trim . }}
          {{- if $attribute }}
          {{- $parts := regexSplit "=" $attribute 2 }}
          {{- if ne (len $parts) 2 }}
          {{- fail (printf "%s: invalid resource attribute %s (expected key=value)" $.name $attribute) }}
          {{- end }}
          {{- $_ := set $resourceAttributes (trim (index $parts 0)) (index $parts 1) }}
          {{- end }}
          {{- end }}
          {{- end }}
          {{- $_ := mergeOverwrite $resourceAttributes (include "otel-demo.podResourceAttributes" .root | fromYaml) }}
          {{- if $rollout.enabled }}
          {{- $_ := set $resourceAttributes "rollout.pod_template_hash" "$(ROLLOUT_POD_TEMPLATE_HASH)" }}
          {{- $_ := set $resourceAttributes "service.version" .service.image.tag }}
          {{- end }}
          {{- $attributes := list }}
          {{- range $key, $value := $resourceAttributes }}
          {{- $attributes = append $attributes (printf "%s=%s" $key $value) }}
          {{- end }}
          {{- $_ := set $mergedEnv "OTEL_RESOURCE_ATTRIBUTES" (dict "value" (join "," $attributes)) }}
          {{/* EXTRA is a chart input; SDKs consume the merged standard variable. */}}
          {{- $_ := unset $mergedEnv "OTEL_RESOURCE_ATTRIBUTES_EXTRA" }}

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
{{- if lt (int .service.hpa.maxReplicas) (int .service.hpa.minReplicas) }}
{{- fail (printf "%s: HPA maxReplicas must be at least minReplicas" .name) }}
{{- end }}
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
  {{- with .service.hpa.behavior }}
  behavior:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end }}
{{- end }}

{{- define "otel-demo.servicePDB" -}}
{{- if and .service.pdb .service.pdb.enabled }}
{{- $minimumReplicas := .service.replicaCount }}
{{- if (.service.hpa | default dict).enabled }}
{{- $minimumReplicas = .service.hpa.minReplicas }}
{{- end }}
{{- if lt (int $minimumReplicas) 2 }}
{{- fail (printf "%s: enable a PDB only with at least two replicas (or HPA minReplicas); disable its PDB for a single-replica development override" .name) }}
{{- end }}
{{- if and (ne .service.pdb.minAvailable nil) (not (kindIs "string" .service.pdb.minAvailable)) }}
{{- if ge (int .service.pdb.minAvailable) (int $minimumReplicas) }}
{{- fail (printf "%s: PDB minAvailable must be below the minimum replica count to allow a voluntary eviction" .name) }}
{{- end }}
{{- end }}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "otel-demo.serviceName" . }}
  labels:
    {{- include "otel-demo.labels" .root | nindent 4 }}
    app.kubernetes.io/component: {{ .name }}
spec:
  {{- if ne .service.pdb.minAvailable nil }}
  minAvailable: {{ .service.pdb.minAvailable }}
  {{- else }}
  maxUnavailable: {{ .service.pdb.maxUnavailable }}
  {{- end }}
  unhealthyPodEvictionPolicy: {{ .service.pdb.unhealthyPodEvictionPolicy | default "AlwaysAllow" }}
  selector:
    matchLabels:
      {{- include "otel-demo.serviceSelectorLabels" . | nindent 6 }}
{{- end }}
{{- end }}
