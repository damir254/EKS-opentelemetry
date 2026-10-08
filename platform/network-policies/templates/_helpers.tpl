{{/* Generate both pod directions and Service DNS routes from one connection map. */}}
{{- define "platform-network.connections" -}}
{{- $incoming := dict }}
{{- $outgoing := dict }}
{{- $services := dict }}
{{- range .Values.connections }}
{{- if or (not (hasKey $.Values.workloads .from)) (not (hasKey $.Values.workloads .to)) }}
{{- fail "Every network connection must reference declared workloads" }}
{{- end }}
{{- $source := index $.Values.workloads .from }}
{{- $target := index $.Values.workloads .to }}
{{- $protocol := .protocol | default "TCP" }}
{{- $edge := . }}
{{- $ports := list }}
{{- range .ports }}{{- $ports = append $ports (dict "port" (int .) "protocol" $protocol) }}{{- end }}
{{- $from := dict "namespaceSelector" (dict "matchLabels" (dict "kubernetes.io/metadata.name" $source.namespace)) "podSelector" (dict "matchLabels" $source.selector) }}
{{- $to := dict "namespaceSelector" (dict "matchLabels" (dict "kubernetes.io/metadata.name" $target.namespace)) "podSelector" (dict "matchLabels" $target.selector) }}
{{- $_ := set $incoming .to (append (get $incoming .to | default list) (dict "from" (list $from) "ports" $ports)) }}
{{- $_ := set $outgoing .from (append (get $outgoing .from | default list) (dict "to" (list $to) "ports" $ports)) }}
{{- range $service, $mapping := $target.services }}
{{- $servicePorts := list }}
{{- range $edge.ports }}
{{- if hasKey $mapping (toString .) }}
{{- range index $mapping (toString .) }}{{- $servicePorts = append $servicePorts (dict "port" (int .) "protocol" $protocol) }}{{- end }}
{{- end }}
{{- end }}
{{- if $servicePorts }}
{{- $peer := dict "domainNames" (list (printf "%s.%s.svc.cluster.local" $service $target.namespace)) }}
{{- $_ := set $services $edge.from (append (get $services $edge.from | default list) (dict "to" (list $peer) "ports" $servicePorts)) }}
{{- end }}
{{- end }}
{{- end }}
{{- dict "ingress" $incoming "egress" $outgoing "services" $services | toYaml -}}
{{- end -}}

{{/* Render bounded ingress/egress exceptions using shared network ranges. */}}
{{- define "platform-network.cidrs" -}}
{{- $rules := list }}
{{- range $group, $numbers := .groups }}
{{- if not (hasKey $.ranges $group) }}{{- fail (printf "Unknown CIDR group: %s" $group) }}{{- end }}
{{- $ports := list }}
{{- range $numbers }}{{- $ports = append $ports (dict "port" (int .) "protocol" "TCP") }}{{- end }}
{{- $peers := list }}
{{- range index $.ranges $group }}{{- $peers = append $peers (dict "ipBlock" (dict "cidr" .)) }}{{- end }}
{{- $rules = append $rules (dict $.direction $peers "ports" $ports) }}
{{- end }}
{{- toYaml $rules -}}
{{- end -}}
