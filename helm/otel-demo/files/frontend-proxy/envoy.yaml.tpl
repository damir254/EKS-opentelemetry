{{- $service := index .Values.services "frontend-proxy" -}}
{{- $env := mergeOverwrite (deepCopy .Values.defaultEnv) $service.env -}}
{{- $collectorHost := $env.OTEL_COLLECTOR_HOST.value | replace "$(OTEL_COLLECTOR_NAME)" $env.OTEL_COLLECTOR_NAME.value -}}
{{- $browser := required "frontend-proxy.browserTelemetry is required" $service.browserTelemetry -}}
{{- if or (lt (int $browser.maxRequestBytes) 1) (lt (int $browser.requestsPerSecond) 1) (lt (int $browser.burst) (int $browser.requestsPerSecond)) -}}
{{- fail "frontend-proxy.browserTelemetry requires positive limits and burst >= requestsPerSecond" -}}
{{- end -}}
# Helm-rendered routing/telemetry configuration from the OpenTelemetry demo.
# Source image: ghcr.io/open-telemetry/demo@sha256:b8ca03e80482c08b92a356c22cd45fca6f9dd0a477ef1b0d469134d1cb8c6961
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

static_resources:
  listeners:
    - address:
        socket_address:
          address: {{ $env.ENVOY_ADDR.value | quote }}
          port_value: {{ $env.ENVOY_PORT.value }}
      filter_chains:
        - filters:
            - name: envoy.filters.network.http_connection_manager
              typed_config:
                "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
                codec_type: AUTO
                stat_prefix: ingress_http
                tracing:
                  spawn_upstream_span: true
                  provider:
                    name: envoy.tracers.opentelemetry
                    typed_config:
                      "@type": type.googleapis.com/envoy.config.trace.v3.OpenTelemetryConfig
                      grpc_service:
                        envoy_grpc:
                          cluster_name: opentelemetry_collector_grpc
                        timeout: 0.250s
                      service_name: frontend-proxy
                      resource_detectors:
                        - name: envoy.tracers.opentelemetry.resource_detectors.environment
                          typed_config:
                            "@type": type.googleapis.com/envoy.extensions.tracers.opentelemetry.resource_detectors.v3.EnvironmentResourceDetectorConfig
                route_config:
                  name: local_route
                  virtual_hosts:
                    - name: frontend
                      domains:
                        - "*"
                      typed_per_filter_config:
                        # Buffering is enabled only on the browser trace route.
                        envoy.filters.http.buffer:
                          "@type": type.googleapis.com/envoy.extensions.filters.http.buffer.v3.BufferPerRoute
                          disabled: true
                      routes:
                        - match: { path: "/loadgen" }
                          redirect: { path_redirect: "/loadgen/" }
                        - match: { prefix: "/loadgen/" }
                          route: { cluster: loadgen, prefix_rewrite: "/" }
                        - match:
                            path: /otlp-http/v1/traces
                            headers:
                              - name: ":method"
                                string_match: {exact: POST}
                              - name: content-type
                                string_match:
                                  safe_regex:
                                    regex: '^application/(json|x-protobuf)(;.*)?$'
                              - name: content-encoding
                                treat_missing_header_as_empty: true
                                string_match:
                                  safe_regex: {regex: '^(identity)?$'}
                          route:
                            cluster: opentelemetry_collector_browser
                            prefix_rewrite: /v1/traces
                            timeout: 5s
                            max_stream_duration: {max_stream_duration: 10s}
                          typed_per_filter_config:
                            envoy.filters.http.local_ratelimit:
                              "@type": type.googleapis.com/envoy.extensions.filters.http.local_ratelimit.v3.LocalRateLimit
                              stat_prefix: browser_telemetry
                              token_bucket:
                                max_tokens: {{ int $browser.burst }}
                                tokens_per_fill: {{ int $browser.requestsPerSecond }}
                                fill_interval: 1s
                              filter_enabled: {default_value: {numerator: 100, denominator: HUNDRED}}
                              filter_enforced: {default_value: {numerator: 100, denominator: HUNDRED}}
                            envoy.filters.http.buffer:
                              "@type": type.googleapis.com/envoy.extensions.filters.http.buffer.v3.BufferPerRoute
                              buffer: {max_request_bytes: {{ int $browser.maxRequestBytes }}}
                            envoy.filters.http.fault: &browser_no_fault
                              # Public telemetry cannot request the demo's header-based fault delay.
                              "@type": type.googleapis.com/envoy.extensions.filters.http.fault.v3.HTTPFault
                        - match:
                            path: /otlp-http/v1/traces
                            headers:
                              - name: ":method"
                                string_match: {exact: POST}
                          direct_response:
                            status: 415
                            body: {inline_string: "Use uncompressed OTLP JSON or protobuf.\n"}
                          typed_per_filter_config: {envoy.filters.http.fault: *browser_no_fault}
                        - match: {path: /otlp-http/v1/traces}
                          direct_response: {status: 405}
                          typed_per_filter_config: {envoy.filters.http.fault: *browser_no_fault}
                          response_headers_to_add:
                            - header: {key: allow, value: POST}
                        - match: {path: /otlp-http}
                          direct_response: {status: 404}
                          typed_per_filter_config: {envoy.filters.http.fault: *browser_no_fault}
                        - match: {prefix: /otlp-http/}
                          direct_response: {status: 404}
                          typed_per_filter_config: {envoy.filters.http.fault: *browser_no_fault}
                        - match: { path: "/jaeger" }
                          redirect: { path_redirect: "/jaeger/" }
                        - match: { prefix: "/jaeger/" }
                          route: { cluster: jaeger }
                        - match: { path: "/grafana" }
                          redirect: { path_redirect: "/grafana/" }
                        - match: { prefix: "/grafana/" }
                          route: { cluster: grafana }
                        - match: { path: "/opamp" }
                          redirect: { path_redirect: "/opamp/" }
                        - match: { prefix: "/opamp/" }
                          route: { cluster: opamp, prefix_rewrite: "/" }
                        - match: { path: "/telemetry" }
                          redirect: { path_redirect: "/telemetry/" }
                        - match: { prefix: "/telemetry/" }
                          route:
                            { cluster: telemetry-docs, prefix_rewrite: "/" }
                        - match: { prefix: "/images/" }
                          route:
                            { cluster: image-provider, prefix_rewrite: "/" }
                        - match: { prefix: "/flagservice/" }
                          route:
                            {
                              cluster: flagservice,
                              prefix_rewrite: "/",
                              timeout: 0s,
                            }
                        - match: { prefix: "/feature" }
                          route:
                            cluster: flagd-ui
                            prefix_rewrite: "/"
                            upgrade_configs:
                              - upgrade_type: websocket
                        - match: { path: "/profiles" }
                          redirect: { path_redirect: "/profiles/" }
                        - match: { prefix: "/profiles/" }
                          route: { cluster: profiles }
                        - match: { path: "/chatbot" }
                          redirect: { path_redirect: "/chatbot/" }
                        - match: { prefix: "/chatbot/" }
                          route:
                            cluster: chatbot
                            prefix_rewrite: "/"
                            timeout: 0s
                            upgrade_configs:
                              - upgrade_type: websocket
                        - match: { prefix: "/" }  # Default/catch-all route - keep last since prefix:"/" matches everything
                          route: { cluster: frontend }
                http_filters:
                  - name: envoy.filters.http.local_ratelimit
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.local_ratelimit.v3.LocalRateLimit
                      stat_prefix: browser_telemetry
                      # Only the trace route supplies/enables its token bucket.
                      filter_enabled: {default_value: {numerator: 0, denominator: HUNDRED}}
                  - name: envoy.filters.http.buffer
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.buffer.v3.Buffer
                      max_request_bytes: {{ int $browser.maxRequestBytes }}
                  - name: envoy.filters.http.fault
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.fault.v3.HTTPFault
                      max_active_faults: 100
                      delay:
                        header_delay: {}
                        percentage:
                          numerator: 100
                  - name: envoy.filters.http.router
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router
                access_log:
                  - name: envoy.access_loggers.open_telemetry
                    typed_config:
                      "@type": "type.googleapis.com/envoy.extensions.access_loggers.open_telemetry.v3.OpenTelemetryAccessLogConfig"
                      stat_prefix: otel_envoy_access_log
                      log_name: "otel_envoy_access_log"
                      grpc_service:
                        envoy_grpc:
                          cluster_name: opentelemetry_collector_grpc
                      body:
                        # yamllint disable-line rule:line-length
                        string_value: "[%START_TIME%] \"%REQ(:METHOD)% %REQ(X-ENVOY-ORIGINAL-PATH?:PATH)% %PROTOCOL%\" %RESPONSE_CODE% %RESPONSE_FLAGS% %RESPONSE_CODE_DETAILS% %CONNECTION_TERMINATION_DETAILS% \"%UPSTREAM_TRANSPORT_FAILURE_REASON%\" %BYTES_RECEIVED% %BYTES_SENT% %DURATION% %RESP(X-ENVOY-UPSTREAM-SERVICE-TIME)% \"%REQ(X-FORWARDED-FOR)%\" \"%REQ(USER-AGENT)%\" \"%REQ(X-REQUEST-ID)%\" \"%REQ(:AUTHORITY)%\" \"%UPSTREAM_HOST%\" %UPSTREAM_CLUSTER% %UPSTREAM_LOCAL_ADDRESS% %DOWNSTREAM_LOCAL_ADDRESS% %DOWNSTREAM_REMOTE_ADDRESS% %REQUESTED_SERVER_NAME% %ROUTE_NAME%\n"
                      resource_attributes:
                        values:
                          - key: "service.name"
                            value:
                              string_value: frontend-proxy
                          - key: "service.namespace"
                            value:
                              string_value: {{ .Release.Namespace | quote }}
                      attributes:
                        values:
                          - key: "destination.address"
                            value:
                              string_value: "%UPSTREAM_REMOTE_ADDRESS_WITHOUT_PORT%"
                          - key: "event.name"
                            value:
                              string_value: "proxy.access"
                          - key: "server.address"
                            value:
                              string_value: "%DOWNSTREAM_LOCAL_ADDRESS%"
                          - key: "source.address"
                            value:
                              string_value: "%DOWNSTREAM_REMOTE_ADDRESS_WITHOUT_PORT%"
                          - key: "upstream.cluster"
                            value:
                              string_value: "%UPSTREAM_CLUSTER%"
                          - key: "upstream.host"
                            value:
                              string_value: "%UPSTREAM_HOST%"
                          - key: "user_agent.original"
                            value:
                              string_value: "%REQ(USER-AGENT)%"
                          - key: "url.full"
                            value:
                              string_value: "%REQ(:SCHEME)%://%REQ(:AUTHORITY)%%REQ(:PATH)%"
                          - key: "url.path"
                            value:
                              string_value: "%REQ(:PATH)%"
                          - key: "url.query"
                            value:
                              string_value: "%REQ(:QUERY)%"
                          - key: "url.template"
                            value:
                              string_value: "%ROUTE_NAME%"
  clusters:
    - name: opentelemetry_collector_grpc
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      typed_dns_resolver_config: &dns_resolver
        name: envoy.network.dns_resolver.cares
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.network.dns_resolver.cares.v3.CaresDnsResolverConfig
      typed_extension_protocol_options:
        envoy.extensions.upstreams.http.v3.HttpProtocolOptions:
          "@type": type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions
          explicit_http_config:
            http2_protocol_options: {}
      load_assignment:
        cluster_name: opentelemetry_collector_grpc
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {{ $collectorHost | quote }}
                      port_value: {{ $env.OTEL_COLLECTOR_PORT_GRPC.value }}
    - name: opentelemetry_collector_browser
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      typed_dns_resolver_config: *dns_resolver
      load_assignment:
        cluster_name: opentelemetry_collector_browser
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {{ $collectorHost | quote }}
                      port_value: {{ $env.OTEL_COLLECTOR_PORT_BROWSER_HTTP.value }}
    - name: frontend
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      typed_dns_resolver_config: *dns_resolver
      load_assignment:
        cluster_name: frontend
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {{ $env.FRONTEND_HOST.value | quote }}
                      port_value: {{ $env.FRONTEND_PORT.value }}
    - name: image-provider
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      typed_dns_resolver_config: *dns_resolver
      load_assignment:
        cluster_name: image-provider
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {{ $env.IMAGE_PROVIDER_HOST.value | quote }}
                      port_value: {{ $env.IMAGE_PROVIDER_PORT.value }}
    - name: flagservice
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      typed_dns_resolver_config: *dns_resolver
      load_assignment:
        cluster_name: flagservice
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {{ $env.FLAGD_HOST.value | quote }}
                      port_value: {{ $env.FLAGD_PORT.value }}
    - name: flagd-ui
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      typed_dns_resolver_config: *dns_resolver
      load_assignment:
        cluster_name: flagd-ui
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {{ $env.FLAGD_UI_HOST.value | quote }}
                      port_value: {{ $env.FLAGD_UI_PORT.value }}
    - name: loadgen
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      typed_dns_resolver_config: *dns_resolver
      load_assignment:
        cluster_name: loadgen
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {{ $env.LOCUST_WEB_HOST.value | quote }}
                      port_value: {{ $env.LOCUST_WEB_PORT.value }}
    - name: grafana
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      typed_dns_resolver_config: *dns_resolver
      load_assignment:
        cluster_name: grafana
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {{ $env.GRAFANA_HOST.value | quote }}
                      port_value: {{ $env.GRAFANA_PORT.value }}
    - name: jaeger
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      typed_dns_resolver_config: *dns_resolver
      load_assignment:
        cluster_name: jaeger
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {{ $env.JAEGER_HOST.value | quote }}
                      port_value: {{ $env.JAEGER_UI_PORT.value }}
    - name: opamp
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      dns_failure_refresh_rate: { base_interval: 10s, max_interval: 30s }
      typed_dns_resolver_config: *dns_resolver
      load_assignment:
        cluster_name: opamp
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {{ $env.OPAMP_SERVER_HOST.value | quote }}
                      port_value: {{ $env.OPAMP_SERVER_UI_PORT.value }}
    - name: telemetry-docs
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      typed_dns_resolver_config: *dns_resolver
      load_assignment:
        cluster_name: telemetry-docs
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {{ $env.TELEMETRY_DOCS_HOST.value | quote }}
                      port_value: {{ $env.TELEMETRY_DOCS_PORT.value }}
    - name: chatbot
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      typed_dns_resolver_config: *dns_resolver
      load_assignment:
        cluster_name: chatbot
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {{ $env.CHATBOT_HOST.value | quote }}
                      port_value: {{ $env.CHATBOT_PORT.value }}
    - name: profiles
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      load_assignment:
        cluster_name: profiles
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {{ $env.FIREPIT_HOST.value | quote }}
                      port_value: {{ $env.FIREPIT_PORT.value }}
admin:
  address:
    socket_address:
      address: 0.0.0.0
      port_value: {{ $env.ENVOY_ADMIN_PORT.value }}
layered_runtime:
  layers:
    - name: static_layer_0
      static_layer:
        envoy:
          resource_limits:
            listener:
              example_listener_name:
                connection_limit: 10000
