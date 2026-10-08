"""Evaluate rendered Kubernetes/EKS allowlists for regression checks."""

import ipaddress


def matches(labels, selector):
    if not all(labels.get(key) == value for key, value in selector.get("matchLabels", {}).items()):
        return False
    for item in selector.get("matchExpressions", []):
        key, operator, values = item["key"], item["operator"], item.get("values", [])
        if ((operator == "In" and labels.get(key) not in values)
                or (operator == "NotIn" and labels.get(key) in values)
                or (operator == "Exists" and key not in labels)
                or (operator == "DoesNotExist" and key in labels)):
            return False
    return True


def domain_matches(host, domain):
    return host == domain or (domain.startswith("*.") and host.endswith(domain[1:]) and host != domain[2:])


class Policies:
    def __init__(self, resources):
        self.items = [item for item in resources if item.get("kind") in ("NetworkPolicy", "ApplicationNetworkPolicy")]

    def selected(self, endpoint, direction):
        return [item for item in self.items if item["metadata"]["namespace"] == endpoint["namespace"]
                and direction.capitalize() in item["spec"].get("policyTypes", ["Ingress"])
                and matches(endpoint.get("labels", {}), item["spec"]["podSelector"])]

    def permits(self, endpoint, peer, direction, port, protocol="TCP"):
        policies = self.selected(endpoint, direction)
        if not policies:
            return True
        for policy in policies:
            for rule in policy["spec"].get(direction) or []:
                ports = rule.get("ports")
                if ports and not any(p.get("protocol", "TCP") == protocol and p.get("port") == port for p in ports):
                    continue
                peers = rule.get("from" if direction == "ingress" else "to")
                if not peers:
                    return True
                for candidate in peers:
                    if not candidate:
                        return True
                    if "ipBlock" in candidate:
                        address = ipaddress.ip_address(peer.get("ip", "192.0.2.1"))
                        block = candidate["ipBlock"]
                        if address in ipaddress.ip_network(block["cidr"]) and not any(address in ipaddress.ip_network(c) for c in block.get("except", [])):
                            return True
                    elif "domainNames" in candidate:
                        if any(domain_matches(peer.get("domain", ""), name) for name in candidate["domainNames"]):
                            return True
                    else:
                        namespace = candidate.get("namespaceSelector")
                        if namespace is None and peer.get("namespace") != endpoint["namespace"]:
                            continue
                        if namespace is not None and (not peer.get("namespace") or not matches({"kubernetes.io/metadata.name": peer["namespace"]}, namespace)):
                            continue
                        if matches(peer.get("labels", {}), candidate.get("podSelector", {})):
                            return True
        return False

    def connection(self, source, target, port, protocol="TCP"):
        return self.permits(source, target, "egress", port, protocol) and self.permits(target, source, "ingress", port, protocol)


def endpoint(workload):
    return {"namespace": workload["namespace"], "labels": workload["selector"], "ip": "10.0.10.200"}


def validate(resources, config):
    policies = Policies(resources)
    isolated = {name for name, settings in config["namespaces"].items() if settings["defaultDeny"]}
    for namespace in isolated:
        denies = [p for p in policies.items if p["metadata"]["namespace"] == namespace
                  and p["spec"]["podSelector"] == {} and set(p["spec"]["policyTypes"]) == {"Ingress", "Egress"}
                  and not p["spec"].get("ingress") and not p["spec"].get("egress")]
        if not denies:
            raise ValueError(f"{namespace}: platform default-deny is missing")
    for policy in policies.items:
        if policy["metadata"]["namespace"] not in isolated:
            continue
        for direction in ("ingress", "egress"):
            for rule in policy["spec"].get(direction) or []:
                peers = rule.get("from" if direction == "ingress" else "to")
                unrestricted = not peers or any(not peer or peer.get("ipBlock", {}).get("cidr") in ("0.0.0.0/0", "::/0") for peer in peers)
                dns_only = direction == "egress" and rule.get("ports") and all(p.get("port") == 53 for p in rule["ports"])
                if not rule or (unrestricted and not dns_only):
                    raise ValueError("Platform policies must not allow all traffic")
    for name, workload in config["workloads"].items():
        if workload.get("managed", True):
            for direction in ("ingress", "egress"):
                if not policies.selected(endpoint(workload), direction):
                    raise ValueError(f"{name}: missing {direction} policy")
    for edge in config["connections"]:
        source, target = [endpoint(config["workloads"][edge[key]]) for key in ("from", "to")]
        target_config = config["workloads"][edge["to"]]
        for port in edge["ports"]:
            if not policies.connection(source, target, port, edge.get("protocol", "TCP")):
                raise ValueError(f"Required network connection blocked: {edge['from']} -> {edge['to']}:{port}")
            for service, mapping in target_config.get("services", {}).items():
                domain = f"{service}.{target_config['namespace']}.svc.cluster.local"
                for service_port in mapping.get(str(port), []):
                    if not policies.permits(source, {"domain": domain}, "egress", service_port, edge.get("protocol", "TCP")):
                        raise ValueError(f"Required Service connection blocked: {edge['from']} -> {domain}:{service_port}")
    for resource in resources:
        if resource.get("kind") not in ("Deployment", "StatefulSet", "DaemonSet", "Job", "Prometheus", "Alertmanager"):
            continue
        namespace = resource["metadata"].get("namespace")
        if namespace not in isolated:
            continue
        if resource["kind"] in ("Prometheus", "Alertmanager"):
            name = resource["metadata"]["name"]
            labels = {"app.kubernetes.io/name": resource["kind"].lower(),
                      "operator.prometheus.io/name" if resource["kind"] == "Prometheus" else "alertmanager": name}
        else:
            template = resource["spec"]["template"]
            if template["spec"].get("hostNetwork"):
                continue
            labels = template["metadata"].get("labels", {})
        pod = {"namespace": namespace, "labels": labels}
        if not any(p["spec"]["podSelector"] and matches(labels, p["spec"]["podSelector"]) for p in policies.selected(pod, "egress")):
            raise ValueError(f"{namespace}/{resource['metadata']['name']}: no workload egress allowlist (including hooks)")
