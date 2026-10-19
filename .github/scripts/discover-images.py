"""Discover public and private runtime images from the configured GitOps sources."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]


class ManifestLoader(yaml.SafeLoader):
    pass


# Some chart CRD enums contain a bare '=' that PyYAML tags specially.
ManifestLoader.add_constructor("tag:yaml.org,2002:value", lambda loader, node: loader.construct_scalar(node))


def documents(path):
    return [doc for doc in yaml.load_all(path.read_text(), Loader=ManifestLoader) if doc is not None]


def run(args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def render_sources(work, argocd_version):
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
            flags += ["--values", value_path]
        if "valuesObject" in helm or "values" in helm:
            inline = work / f"{name}-values.yaml"
            inline.write_text(yaml.safe_dump(helm["valuesObject"]) if "valuesObject" in helm else helm["values"])
            flags += ["--values", inline]

        destination = rendered / f"{render_count:02d}-{name}.yaml"
        render_count += 1
        with destination.open("w") as output:
            run(["helm", "template", helm.get("releaseName", name), chart, "--namespace", namespace,
                 "--kube-version", version + ".0", *flags], stdout=output)
        resources.extend(documents(destination))

    render("argocd", "argocd", {"chart": "argo-cd", "repoURL": "https://argoproj.github.io/argo-helm",
           "targetRevision": argocd_version, "helm": {"valueFiles": ["$values/platform/argocd/values.yaml"]}}, {"values": {}})
    for path in sorted((ROOT / "platform/argocd/applications").glob("*.yaml")):
        for application in documents(path):
            if application.get("kind") != "Application":
                continue
            spec = application["spec"]
            sources = spec.get("sources", [spec.get("source")])
            refs = {source["ref"]: source for source in sources if source and "ref" in source}
            for source in sources:
                if "chart" in source or "helm" in source:
                    render(application["metadata"]["name"], spec["destination"]["namespace"], source, refs)
                elif "path" in source:
                    directory = ROOT / source["path"]
                    include = source.get("directory", {}).get("include", "*.yaml")
                    if "{" in include or "," in include:
                        raise ValueError(f"Unsupported directory include {include}; extend discovery explicitly")
                    for manifest in sorted(directory.glob(include)):
                        resources.extend(documents(manifest))

    return resources, render_count


def image_inventory(resources):
    """Collect image references, including images injected by controllers."""
    images = set()

    def add(image):
        if not re.fullmatch(r"[a-zA-Z0-9._/:@-]+", image) or not re.search(r"[:@]", image.rsplit("/", 1)[-1]):
            raise ValueError(f"Unresolved container image: {image!r}")
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
        # Istio injects these containers after Helm has rendered the workloads.
        if resource.get("kind") == "ConfigMap" and "injector" in resource["metadata"]["name"]:
            values = yaml.safe_load(resource.get("data", {}).get("values", "{}"))
            settings = values.get("global", {})
            for component in ("proxy", "proxy_init"):
                image = settings.get(component, {}).get("image")
                if image:
                    if "/" not in image:
                        suffix = f'-{settings["variant"]}' if settings.get("variant") else ""
                        image = f'{settings["hub"]}/{image}:{settings["tag"]}{suffix}'
                    add(image)
    if not images or len(images) > 256:
        raise ValueError(f"Unexpected image inventory size: {len(images)}")
    return {"include": [
        {"image": image, "id": image.split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0][:35]
         + "-" + hashlib.sha256(image.encode()).hexdigest()[:12]}
        for image in sorted(images)
    ]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--argocd-version", default="10.9.6")
    parser.add_argument("--output", type=Path, required=True, help="Write the image scan matrix as JSON")
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="image-discovery-") as directory:
        resources, count = render_sources(Path(directory), options.argocd_version)
        inventory = image_inventory(resources)
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(json.dumps(inventory, separators=(",", ":")) + "\n")
        print(f"Discovered {len(inventory['include'])} images from {count} Helm sources.")
