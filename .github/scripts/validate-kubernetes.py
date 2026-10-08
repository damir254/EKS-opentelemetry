"""Render the actual Argo CD sources and validate every GVK, including CRDs."""

import argparse
from copy import deepcopy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[2]


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"Duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)
# Some CRD enums contain a bare '='. Kubernetes accepts it as a string, while
# PyYAML's YAML 1.1 resolver assigns a special tag without a default constructor.
UniqueLoader.add_constructor("tag:yaml.org,2002:value", lambda loader, node: loader.construct_scalar(node))


def documents(path):
    return [doc for doc in yaml.load_all(path.read_text(), Loader=UniqueLoader) if doc is not None]


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def json_schema(schema, envelope=True):
    """Translate nullable OpenAPI fields and reject unknown structured fields."""
    schema = deepcopy(schema)

    def convert(node):
        if isinstance(node, list):
            for child in node:
                convert(child)
        elif isinstance(node, dict):
            nullable = node.pop("nullable") if isinstance(node.get("nullable"), bool) else False
            if nullable and isinstance(node.get("type"), str):
                node["type"] = [node["type"], "null"]
            if node.get("type") == "object" and node.get("properties") and not node.get("x-kubernetes-preserve-unknown-fields"):
                node.setdefault("additionalProperties", False)
            for child in node.values():
                convert(child)

    convert(schema)
    if envelope:
        properties = schema.setdefault("properties", {})
        properties.setdefault("apiVersion", {"type": "string"})
        properties.setdefault("kind", {"type": "string"})
        # Kubernetes metadata has its own API schema; CRDs normally declare only object.
        properties["metadata"] = {"type": "object"}
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    return schema


def validate_images(resources):
    """Owned workloads use digests, or immutable ECR commit/release tags."""
    for resource in resources:
        spec = resource.get("spec", {})
        pod = spec if resource.get("kind") == "Pod" else spec.get("template", {}).get("spec", {})
        for container in pod.get("containers", []) + pod.get("initContainers", []):
            image = container["image"]
            pinned = re.search(r"@sha256:[0-9a-f]{64}$", image)
            immutable_ecr = ".dkr.ecr." in image and re.search(r":(?:release-)?[0-9a-f]{40}$", image)
            if not pinned and not immutable_ecr:
                raise ValueError(f'{resource["metadata"]["name"]}/{container["name"]}: floating image {image}')


def validate_demo_storage(resources):
    """Keep disposable demo storage durable during node consolidation."""
    objects = {(item.get("kind"), item["metadata"]["name"]): item for item in resources}
    mounts = {"astronomy-db": "/var/lib/postgresql/data", "kafka": "/var/lib/kafka/data",
              "valkey-cart": "/data"}
    for name, mount_path in mounts.items():
        if ("StatefulSet", name) not in objects and ("Deployment", name) not in objects:
            continue
        if ("Deployment", name) in objects:
            raise ValueError(f"{name}: demo storage requires a StatefulSet")
        spec = objects[("StatefulSet", name)]["spec"]
        if spec.get("replicas") != 1:
            raise ValueError(f"{name}: additional replicas require application-level replication")
        if spec.get("persistentVolumeClaimRetentionPolicy") != {"whenDeleted": "Delete", "whenScaled": "Retain"}:
            raise ValueError(f"{name}: delete PVCs with the StatefulSet, retain them during scale-down")
        pod = spec["template"]
        if pod["metadata"].get("annotations", {}).get("karpenter.sh/do-not-disrupt") == "true":
            raise ValueError(f"{name}: do-not-disrupt blocks demo consolidation")
        claims = {claim["metadata"]["name"]: claim["spec"] for claim in spec.get("volumeClaimTemplates", [])}
        if set(claims) != {"data"} or claims["data"].get("storageClassName") != "gp3" or claims["data"].get("accessModes") != ["ReadWriteOnce"]:
            raise ValueError(f"{name}: requires a gp3 ReadWriteOnce data claim")
        storage = objects.get(("StorageClass", "gp3"), {}).get("reclaimPolicy")
        if storage != "Delete":
            raise ValueError("gp3 must delete the EBS volume after PVC deletion")
        container = pod["spec"]["containers"][0]
        if {"name": "data", "mountPath": mount_path} not in container.get("volumeMounts", []):
            raise ValueError(f"{name}: application data must be mounted on the PVC")
        labels = pod["metadata"]["labels"]
        for service_name in [name, spec["serviceName"]]:
            service = objects[("Service", service_name)]["spec"]
            selector = service["selector"]
            if selector.get("app.kubernetes.io/workload-kind") != "StatefulSet" or not all(labels.get(key) == value for key, value in selector.items()):
                raise ValueError(f"{name}: Service must select only new StatefulSet pods during migration")
        if objects[("Service", spec["serviceName"])]["spec"].get("clusterIP") != "None":
            raise ValueError(f"{name}: governing Service must be headless")
        env = {item["name"]: item.get("value") for item in container.get("env", [])}
        if name == "astronomy-db" and env.get("PGDATA") != mount_path + "/pgdata":
            raise ValueError("PostgreSQL PGDATA must use a PVC subdirectory")
        if name == "kafka" and any(not (env.get(key) or "").startswith(mount_path + "/") for key in ["KAFKA_LOG_DIRS", "KAFKA_METADATA_LOG_DIR"]):
            raise ValueError("Kafka messages and KRaft metadata must both use the PVC")
        if name == "valkey-cart":
            args = container.get("args", [])
            for key, value in [("--dir", mount_path), ("--appendonly", "yes"), ("--appendfsync", "everysec")]:
                if key not in args or args[args.index(key) + 1:args.index(key) + 2] != [value]:
                    raise ValueError("Valkey must persist AOF data on the PVC")


def validate_grafana_admin(resources):
    deployment = next((item for item in resources if item.get("kind") == "Deployment"
                       and item["metadata"]["name"] == "monitoring-grafana"), None)
    if deployment is None:
        return
    if any(item.get("kind") == "Secret" and item["metadata"]["name"] == "monitoring-grafana"
           for item in resources):
        raise ValueError("Grafana must not render a generated admin Secret alongside its persistent database")
    provider = next((item for item in resources if item.get("kind") == "ExternalSecret"
                     and item["metadata"]["name"] == "monitoring-grafana-admin"), None)
    if provider is None:
        raise ValueError("Grafana's stable admin Secret requires an ExternalSecret")
    mappings = {item["secretKey"]: item["remoteRef"] for item in provider["spec"]["data"]}
    for key, property_name in [("admin-user", "grafana_admin_user"), ("admin-password", "grafana_admin_password")]:
        if mappings.get(key) != {"key": "grafana-db-credentials", "property": property_name}:
            raise ValueError("Grafana admin credentials must come from their dedicated AWS bundle fields")
    def wave(resource):
        return int(resource["metadata"].get("annotations", {}).get("argocd.argoproj.io/sync-wave", "0"))
    hook = next(item for item in resources if item.get("kind") == "Job"
                and item["metadata"]["name"] == "grafana-db-bootstrap")
    if not wave(hook) < wave(provider) < wave(deployment):
        raise ValueError("Initialize credentials, then sync the admin Secret, then start Grafana")
    for container in deployment["spec"]["template"]["spec"]["containers"]:
        fields = {env["name"]: env for env in container.get("env", [])}
        for variable, key in [("GF_SECURITY_ADMIN_USER", "admin-user"), ("GF_SECURITY_ADMIN_PASSWORD", "admin-password"),
                              ("REQ_USERNAME", "admin-user"), ("REQ_PASSWORD", "admin-password")]:
            if variable in fields:
                reference = fields[variable].get("valueFrom", {}).get("secretKeyRef", {})
                if reference != {"name": "monitoring-grafana-admin", "key": key}:
                    raise ValueError(f'Grafana/{container["name"]}/{variable} must use the stable admin Secret')


def public_images(resources):
    """Include workloads, init containers and images created by controllers."""
    images = set()

    def add(image):
        if not re.fullmatch(r"[a-zA-Z0-9._/:@-]+", image) or not re.search(r"[:@]", image.rsplit("/", 1)[-1]):
            raise ValueError(f"Unresolved container image: {image!r}")
        # Maintained private ECR images have their own build/scan release gate.
        if ".dkr.ecr." not in image:
            images.add(image)

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "image" and isinstance(item, str):
                    add(item)
                elif key == "args" and isinstance(item, list):
                    for arg in item:
                        if isinstance(arg, str) and arg.startswith("--prometheus-config-reloader="):
                            add(arg.split("=", 1)[1])
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for resource in resources:
        walk(resource.get("spec", {}))
        # Istio adds proxy/init containers at admission, after Helm rendering.
        if resource.get("kind") == "ConfigMap" and "injector" in resource["metadata"]["name"]:
            values = yaml.safe_load(resource.get("data", {}).get("values", "{}"))
            global_values = values.get("global", {})
            for component in ("proxy", "proxy_init"):
                image = global_values.get(component, {}).get("image")
                if image:
                    if "/" not in image:
                        variant = global_values.get("variant")
                        suffix = f"-{variant}" if variant else ""
                        image = f'{global_values["hub"]}/{image}:{global_values["tag"]}{suffix}'
                    add(image)
    if not images or len(images) > 256:
        raise ValueError(f"Unexpected image inventory size: {len(images)}")
    return {"include": [
        {"image": image, "id": image.split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0][:35]
         + "-" + hashlib.sha256(image.encode()).hexdigest()[:12]}
        for image in sorted(images)
    ]}


def validate_demo_access(resources):
    config = documents(ROOT / "helm/otel-demo/environments/dev.yaml")[0]
    settings = config["ingress"]
    ingress = next((item for item in resources if item.get("kind") == "Ingress"
                    and item["metadata"]["name"] == settings["name"]), None)
    if ingress is None:
        return
    annotations = ingress["metadata"]["annotations"]
    if json.loads(annotations["alb.ingress.kubernetes.io/listen-ports"]) != [{"HTTPS": 443}]:
        raise ValueError("Demo/Locust must expose only HTTPS on port 443")
    if ingress["spec"]["ingressClassName"] != "platform-dashboards":
        raise ValueError("Demo/Locust must use the shared platform-dashboards class")
    if "alb.ingress.kubernetes.io/certificate-arn" in annotations:
        raise ValueError("Shared certificates belong on IngressClassParams")
    expected = {settings["host"]: "frontend-proxy"}
    locust = settings["loadGenerator"]
    if locust["enabled"]:
        expected[locust["host"]] = "load-generator"
        cidrs = locust["allowedCIDRs"]
        for cidr in cidrs:
            network = ipaddress.ip_network(cidr)
            if network.version != 4 or network.prefixlen == 0:
                raise ValueError("Locust requires restricted IPv4 client CIDRs")
        condition = json.loads(annotations.get("alb.ingress.kubernetes.io/conditions.load-generator", "[]"))
        if condition != [{"field": "source-ip", "sourceIpConfig": {"values": cidrs}}]:
            raise ValueError("Locust requires its ALB source-IP condition; a hostname alone is not protection")
        policy = next((item for item in resources if item.get("kind") == "NetworkPolicy"
                       and item["metadata"]["name"] == "otel-demo-allow-alb-load-generator"), None)
        if policy is None or policy["spec"]["ingress"] != [{
            "from": [{"ipBlock": {"cidr": config["networkPolicy"]["albSourceCidr"]}}],
            "ports": [{"protocol": "TCP", "port": 8089}],
        }]:
            raise ValueError("Locust requires a VPC-source ingress NetworkPolicy on TCP 8089")
    rules = ingress["spec"]["rules"]
    actual = {rule.get("host"): [path["backend"]["service"]["name"] for path in rule["http"]["paths"]]
              for rule in rules}
    if len(rules) != len(expected) or actual != {host: [service] for host, service in expected.items()}:
        raise ValueError("Demo and Locust must use separate, explicit host rules")
    # A second, unrestricted Ingress must not bypass the protected Locust rule.
    for item in resources:
        if item.get("kind") == "Ingress" and item is not ingress and item["metadata"].get("namespace") == "dev":
            for rule in item["spec"].get("rules", []):
                if any(path["backend"].get("service", {}).get("name") == "load-generator"
                       for path in rule["http"]["paths"]):
                    raise ValueError("An additional Ingress must not expose Locust")
    external_dns = next((item for item in resources if item.get("kind") == "Deployment"
                         and item["metadata"]["name"] == "external-dns"), None)
    if external_dns:
        args = external_dns["spec"]["template"]["spec"]["containers"][0]["args"]
        classes = [arg for arg in args if arg.startswith("--ingress-class=")]
        if classes != ["--ingress-class=platform-dashboards"]:
            raise ValueError("ExternalDNS must reconcile the single shared Ingress class")


def validate_shared_alb(resources):
    settings = documents(ROOT / "platform/dashboard-access/values.yaml")[0]
    params = [item for item in resources if item.get("kind") == "IngressClassParams"
              and item["apiVersion"] == "eks.amazonaws.com/v1"]
    if len(params) != 1 or params[0]["metadata"]["name"] != "platform-dashboards":
        raise ValueError("Exactly one Auto Mode ALB class must own the platform-dashboards group")
    spec = params[0]["spec"]
    if spec.get("group", {}).get("name") != "platform-dashboards":
        raise ValueError("Retain the platform-dashboards group name to reuse its ALB")
    if spec.get("listeners") != [{"port": 443, "protocol": "HTTPS"}]:
        raise ValueError("The shared ALB must expose HTTPS only")
    if spec.get("certificateARNs") != settings["certificateARNs"] or not settings["certificateARNs"]:
        raise ValueError("The shared ALB must attach its configured dashboard and demo certificates")
    selector = spec.get("namespaceSelector", {}).get("matchExpressions", [])
    if selector != [{"key": "kubernetes.io/metadata.name", "operator": "In",
                     "values": ["argocd", "monitoring", "dev", "identity"]}]:
        raise ValueError("Only the approved platform/demo/identity namespaces may join the shared ALB")
    classes = [item for item in resources if item.get("kind") == "IngressClass"
               and item["spec"].get("controller") == "eks.amazonaws.com/alb"]
    if (len(classes) != 1 or classes[0]["metadata"]["name"] != "platform-dashboards"
            or classes[0]["spec"]["parameters"]["name"] != "platform-dashboards"):
        raise ValueError("All platform ingresses require one shared Auto Mode IngressClass")
    cidrs = settings["allowedCIDRs"]
    if not cidrs or any(ipaddress.ip_network(cidr).version != 4 or ipaddress.ip_network(cidr).prefixlen == 0
                        for cidr in cidrs):
        raise ValueError("Dashboard source-IP restrictions must be explicit IPv4 networks")
    for name, namespace, service, host in [
        ("argocd-dashboard", "argocd", "argocd-server", settings["argocdHostname"]),
        ("grafana-dashboard", "monitoring", "monitoring-grafana", settings["grafanaHostname"]),
    ]:
        ingress = next(item for item in resources if item.get("kind") == "Ingress"
                       and item["metadata"]["name"] == name and item["metadata"]["namespace"] == namespace)
        annotations = ingress["metadata"]["annotations"]
        condition = json.loads(annotations.get(f"alb.ingress.kubernetes.io/conditions.{service}", "[]"))
        if condition != [{"field": "source-ip", "sourceIpConfig": {"values": cidrs}}]:
            raise ValueError(f"{name} requires its own ALB source-IP condition")
        if [rule.get("host") for rule in ingress["spec"]["rules"]] != [host]:
            raise ValueError(f"{name} requires an explicit dashboard hostname")
    for item in resources:
        if item.get("kind") == "Ingress" and item["spec"].get("ingressClassName") != "platform-dashboards":
            raise ValueError("Every platform/demo Ingress must use the shared ALB class")


def validate_keycloak(resources):
    """Prevent public admin/management exposure and accidental OIDC client weakening."""
    settings = documents(ROOT / "platform/dashboard-access/values.yaml")[0]
    keycloak_values = documents(ROOT / "platform/keycloak/values.yaml")[0]
    realm = json.loads((ROOT / "platform/keycloak/realm.json").read_text())
    if keycloak_values["hostname"] != settings["keycloakHostname"] or realm["realm"] != "platform":
        raise ValueError("Keycloak's hostname and application realm must match shared access configuration")
    if realm.get("registrationAllowed") or not realm.get("bruteForceProtected"):
        raise ValueError("Keycloak requires closed registration and brute-force protection")
    clients = {item["clientId"]: item for item in realm["clients"]}
    callbacks = {
        "argocd": {f'https://{settings["argocdHostname"]}/auth/callback',
                   f'https://{settings["argocdHostname"]}/pkce/verify', 'http://localhost:8085/auth/callback'},
        "grafana": {f'https://{settings["grafanaHostname"]}/login/generic_oauth'},
    }
    for name, expected in callbacks.items():
        client = clients[name]
        if (set(client["redirectUris"]) != expected or client.get("directAccessGrantsEnabled")
                or client.get("implicitFlowEnabled") or client.get("fullScopeAllowed")
                or client["attributes"].get("pkce.code.challenge.method") != "S256"):
            raise ValueError("Dashboard clients require exact callbacks, PKCE and restricted scopes")
    ingress = next(item for item in resources if item.get("kind") == "Ingress"
                   and item["metadata"]["name"] == "keycloak-access")
    if ingress["metadata"]["namespace"] != "identity" or len(ingress["spec"]["rules"]) != 1:
        raise ValueError("Keycloak requires its own identity namespace and explicit host")
    rule = ingress["spec"]["rules"][0]
    paths = rule["http"]["paths"]
    expected_paths = {"/realms/platform/": "keycloak", "/resources/": "keycloak", "/": "keycloak-admin"}
    if (rule["host"] != settings["keycloakHostname"] or len(paths) != len(expected_paths)
            or {path["path"]: path["backend"]["service"]["name"] for path in paths} != expected_paths
            or any(path["backend"]["service"]["port"] != {"number": 8080} for path in paths)):
        raise ValueError("Expose only application realm/resources publicly; route all other paths to restricted admin")
    condition = ingress["metadata"]["annotations"].get("alb.ingress.kubernetes.io/conditions.keycloak-admin", "[]")
    if json.loads(condition) != [{"field": "source-ip", "sourceIpConfig": {"values": settings["allowedCIDRs"]}}]:
        raise ValueError("Keycloak administration requires the operator source-IP condition")
    for item in resources:
        if item.get("kind") == "Ingress" and item is not ingress:
            for other_rule in item["spec"].get("rules", []):
                for path in other_rule["http"]["paths"]:
                    if path["backend"].get("service", {}).get("name") in {"keycloak", "keycloak-admin"}:
                        raise ValueError("Another Ingress must not bypass Keycloak's access restrictions")
    deployment = next(item for item in resources if item.get("kind") == "Deployment"
                      and item["metadata"]["name"] == "keycloak")
    if deployment["spec"]["replicas"] < 2:
        raise ValueError("Keycloak needs two replicas for node replacement availability")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    fields = {item["name"]: item for item in container["env"]}
    for name in ["KC_DB_PASSWORD", "KC_BOOTSTRAP_ADMIN_CLIENT_SECRET"]:
        if "secretKeyRef" not in fields[name].get("valueFrom", {}):
            raise ValueError("Keycloak credentials must be provided by external Secrets")
    policy = next(item for item in resources if item.get("kind") == "NetworkPolicy"
                  and item["metadata"]["name"] == "keycloak")
    if {"ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]} not in policy["spec"]["egress"]:
        raise ValueError("Keycloak must permit Auto Mode's node DNS resolvers on TCP/UDP 53")


def validate(work, argocd_version, image_inventory=None):
    for directory in ("helm", "platform", ".github/workflows"):
        for path in (ROOT / directory).rglob("*.yaml"):
            if "templates" not in path.parts:
                documents(path)
    for schema in (ROOT / ".github/schemas").rglob("*.json"):
        json.loads(schema.read_text())
    for dockerfile in (ROOT / "src").glob("*/Dockerfile"):
        stages = set()
        for line in dockerfile.read_text().splitlines():
            if line.upper().startswith("FROM "):
                parts = line.split()
                image = parts[2] if parts[1].startswith("--platform=") else parts[1]
                if image not in stages and not re.search(r"@sha256:[0-9a-f]{64}$", image):
                    raise ValueError(f"{dockerfile}: base image must be pinned by digest")
                if len(parts) >= 4 and parts[-2].upper() == "AS":
                    stages.add(parts[-1])

    version = re.search(r'kubernetes_version\s*=\s*"([0-9]+\.[0-9]+)"',
                        (ROOT / "terraform/terraform.tfvars").read_text()).group(1)
    rendered = work / "rendered"
    rendered.mkdir()
    charts = work / "charts"
    charts.mkdir()
    resources = []
    render_count = 0
    chart_cache = Path(os.environ["HELM_CHART_CACHE"]) if os.environ.get("HELM_CHART_CACHE") else None

    def render(name, namespace, source, refs=None):
        nonlocal render_count
        refs = refs or {}
        helm = source.get("helm", {})
        if source.get("chart"):
            revision = str(source["targetRevision"])
            if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][\w.-]+)?", revision):
                raise ValueError(f"{name}: chart version must be exact")
            chart = charts / name / source["chart"]
            cached = chart_cache / source["chart"] if chart_cache else None
            if cached and (cached / "Chart.yaml").is_file() and str(documents(cached / "Chart.yaml")[0]["version"]) == revision:
                chart = cached
            else:
                chart.parent.mkdir(parents=True, exist_ok=True)
                run(["helm", "pull", source["chart"], "--repo", source["repoURL"],
                     "--version", revision, "--untar", "--untardir", chart.parent])
        else:
            chart = ROOT / source["path"]

        flags = []
        for value in helm.get("valueFiles", []):
            if value.startswith("$"):
                ref, value = value[1:].split("/", 1)
                if ref not in refs:
                    raise ValueError(f"Unknown values reference ${ref}")
                value_path = ROOT / value
            else:
                value_path = chart / value
            if not value_path.is_file():
                raise ValueError(f"Missing values file: {value_path}")
            flags += ["--values", value_path]
        if "valuesObject" in helm or "values" in helm:
            inline = work / f"{name}-values.yaml"
            inline.write_text(yaml.safe_dump(helm["valuesObject"]) if "valuesObject" in helm else helm["values"])
            documents(inline)
            flags += ["--values", inline]

        if not source.get("chart"):
            run(["helm", "lint", chart, "--strict", *flags])
        destination = rendered / f"{render_count:02d}-{name}.yaml"
        render_count += 1
        with destination.open("w") as output:
            run(["helm", "template", helm.get("releaseName", name), chart, "--namespace", namespace,
                 "--kube-version", version + ".0", "--include-crds", *flags], stdout=output)
        manifests = documents(destination)
        if not source.get("chart"):
            validate_images(manifests)
        resources.extend(manifests)

    render("argocd", "argocd", {"chart": "argo-cd", "repoURL": "https://argoproj.github.io/argo-helm",
           "targetRevision": argocd_version, "helm": {"valueFiles": ["$values/platform/argocd/values.yaml"]}}, {"values": {}})
    resources.extend(documents(ROOT / "platform/argocd/root-application.yaml"))
    for path in sorted((ROOT / "platform/argocd/applications").glob("*.yaml")):
        for application in documents(path):
            resources.append(application)
            if application.get("kind") != "Application":
                continue
            spec = application["spec"]
            sources = spec.get("sources", [spec.get("source")])
            refs = {s["ref"]: s for s in sources if s and "ref" in s}
            for source in sources:
                if "chart" in source or "helm" in source:
                    render(application["metadata"]["name"], spec["destination"]["namespace"], source, refs)
                elif "path" in source:
                    directory = ROOT / source["path"]
                    include = source.get("directory", {}).get("include", "*.yaml")
                    if "{" in include or "," in include:
                        raise ValueError(f"Unsupported directory include {include}; extend validator explicitly")
                    for manifest in sorted(directory.glob(include)):
                        resources.extend(documents(manifest))

    validate_demo_storage(resources)
    validate_grafana_admin(resources)
    validate_demo_access(resources)
    validate_shared_alb(resources)
    validate_keycloak(resources)
    if image_inventory:
        image_inventory.parent.mkdir(parents=True, exist_ok=True)
        inventory = public_images(resources)
        image_inventory.write_text(json.dumps(inventory, separators=(",", ":")) + "\n")
        print(f"Inventoried {len(inventory['include'])} public images from {render_count} Helm sources.")
        return

    schemas = work / "schemas"
    shutil.copytree(ROOT / ".github/schemas", schemas)
    # The standalone schema registry omits CRD definitions. Derive the missing
    # schema from the matching, version-pinned Kubernetes OpenAPI specification.
    with urllib.request.urlopen(f"https://raw.githubusercontent.com/kubernetes/kubernetes/v{version}.0/api/openapi-spec/swagger.json", timeout=60) as response:
        definitions = json.load(response)["definitions"]
    crd_key = "io.k8s.apiextensions-apiserver.pkg.apis.apiextensions.v1.CustomResourceDefinition"
    reachable = {}

    def collect(key):
        if key in reachable:
            return
        reachable[key] = json_schema(definitions[key], envelope=False)

        def references(value):
            if isinstance(value, dict):
                if isinstance(value.get("$ref"), str):
                    collect(value["$ref"].removeprefix("#/definitions/"))
                for child in value.values():
                    references(child)
            elif isinstance(value, list):
                for child in value:
                    references(child)
        references(definitions[key])

    collect(crd_key)
    directory = schemas / "apiextensions.k8s.io"
    directory.mkdir()
    (directory / "customresourcedefinition_v1.json").write_text(json.dumps({
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$ref": "#/definitions/" + crd_key, "definitions": reachable,
    }))
    for resource in resources:
        if resource.get("kind") == "CustomResourceDefinition":
            spec = resource["spec"]
            for ver in spec["versions"]:
                schema = ver.get("schema", {}).get("openAPIV3Schema")
                if schema:
                    directory = schemas / spec["group"]
                    directory.mkdir(parents=True, exist_ok=True)
                    (directory / f'{spec["names"]["kind"].lower()}_{ver["name"]}.json').write_text(json.dumps(json_schema(schema)))

    # No missing-schema bypass: a new custom kind must bring its CRD/schema.
    combined = work / "all-resources.yaml"
    combined.write_text(yaml.safe_dump_all(resources, sort_keys=False))
    run(["kubeconform", "-strict", "-summary", "-kubernetes-version", version + ".0",
         "-schema-location", str(schemas / "{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"),
         "-schema-location", "default", combined])
    print(f"Validated {render_count} Helm sources and {len(resources)} resources; no schemas skipped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--argocd-version", default="10.9.6")
    parser.add_argument("--image-inventory", type=Path,
                        help="Render public image inventory to JSON instead of validating resource schemas")
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="kubernetes-ci-") as directory:
        validate(Path(directory), options.argocd_version, options.image_inventory)
