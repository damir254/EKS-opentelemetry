"""Report all High/Critical findings; fail on those with available fixes."""

import argparse
from collections import Counter
import html
import json
import os
from pathlib import Path


def check(report, image):
    if report.get("SchemaVersion") != 2 or not isinstance(report.get("Results"), list):
        raise ValueError("Missing or unsupported Trivy image scan results")
    findings = [finding for result in report["Results"]
                for finding in (result.get("Vulnerabilities") or [])
                if finding["Severity"] in ("HIGH", "CRITICAL")]
    blocking = [finding for finding in findings if finding.get("FixedVersion")]
    counts = Counter(finding["Severity"] for finding in findings)
    lines = [f"### {html.escape(image)}", "",
             f"High: {counts['HIGH']}; Critical: {counts['CRITICAL']}; "
             f"fixable: {len(blocking)}; unfixed: {len(findings) - len(blocking)}.", ""]
    if blocking:
        lines += ["| Package | CVE | Severity | Fixed version |", "| --- | --- | --- | --- |"]
        for finding in blocking[:30]:
            values = [finding[key] for key in ("PkgName", "VulnerabilityID", "Severity", "FixedVersion")]
            lines.append("| " + " | ".join(html.escape(value).replace("|", "&#124;").replace("\n", " ")
                                         for value in values) + " |")
    lines += ["", "Full High/Critical results, including unfixed entries, are in the JSON artifact.", ""]
    return bool(blocking), "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--image", required=True)
    options = parser.parse_args()
    failed, summary = check(json.loads(options.report.read_text()), options.image)
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as output:
            output.write(summary)
    raise SystemExit(1 if failed else 0)
