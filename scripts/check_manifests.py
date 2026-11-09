#!/usr/bin/env python3
"""Policy-as-code for rendered manifests (a tiny, dependency-light OPA/Kyverno stand-in).

Usage: python3 scripts/check_manifests.py out/k3d out/sandbox

Hard rules for every workload (ours and upstream):
  - no privileged containers, no hostNetwork / hostPID / hostPath
Hard rules for our own workloads (label app.kubernetes.io/part-of=llm-observability-lab),
reported as warnings for upstream charts:
  - pod or container sets runAsNonRoot: true
  - allowPrivilegeEscalation: false, no privileged containers, capabilities drop ALL
  - CPU and memory limits are set
Extra rules for directories named *sandbox*:
  - no cluster-scoped objects (ClusterRole, ClusterRoleBinding, CRD, Namespace ...)
  - no fixed runAsUser (OpenShift restricted-v2 assigns the UID)
And for every directory: a default-deny NetworkPolicy exists when policies are rendered.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}
CLUSTER_SCOPED = {"ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition", "Namespace",
                  "PersistentVolume", "StorageClass", "MutatingWebhookConfiguration",
                  "ValidatingWebhookConfiguration", "PodSecurityPolicy", "SecurityContextConstraints"}


def pod_spec(doc: dict) -> dict:
    spec = doc.get("spec", {})
    if doc["kind"] == "CronJob":
        spec = spec.get("jobTemplate", {}).get("spec", {})
    return spec.get("template", {}).get("spec", {})


def check_workload(doc: dict, sandbox: bool) -> tuple[list[str], list[str]]:
    """Returns (hard errors, soft findings)."""
    name = f'{doc["kind"]}/{doc["metadata"]["name"]}'
    ps = pod_spec(doc)
    errs: list[str] = []
    soft: list[str] = []
    psc = ps.get("securityContext") or {}
    for flag in ("hostNetwork", "hostPID", "hostIPC"):
        if ps.get(flag):
            errs.append(f"{name}: {flag} is set")
    for vol in ps.get("volumes") or []:
        if "hostPath" in vol:
            errs.append(f"{name}: hostPath volume {vol['name']}")
    if sandbox and psc.get("runAsUser") is not None:
        errs.append(f"{name}: pod runAsUser is fixed ({psc['runAsUser']}), OpenShift assigns it")
    for c in (ps.get("initContainers") or []) + (ps.get("containers") or []):
        cname = f"{name}[{c['name']}]"
        csc = c.get("securityContext") or {}
        if csc.get("privileged"):
            errs.append(f"{cname}: privileged")
        if sandbox and csc.get("runAsUser") is not None:
            errs.append(f"{cname}: runAsUser is fixed ({csc['runAsUser']})")
        if not (csc.get("runAsNonRoot") or psc.get("runAsNonRoot")):
            soft.append(f"{cname}: runAsNonRoot not set")
        if csc.get("allowPrivilegeEscalation") is not False:
            soft.append(f"{cname}: allowPrivilegeEscalation is not false")
        if "ALL" not in [str(x).upper() for x in (csc.get("capabilities") or {}).get("drop", [])]:
            soft.append(f"{cname}: capabilities are not dropped")
        limits = (c.get("resources") or {}).get("limits") or {}
        if not {"cpu", "memory"} <= set(limits):
            soft.append(f"{cname}: cpu/memory limits missing")
    return errs, soft


def check_dir(root: Path) -> list[str]:
    sandbox = "sandbox" in root.name
    docs = []
    for f in sorted(root.rglob("*.yaml")):
        docs += [d for d in yaml.safe_load_all(f.read_text()) if isinstance(d, dict) and d.get("kind")]
    errs: list[str] = []
    warns: list[str] = []
    for d in docs:
        if d["kind"] in WORKLOADS:
            hard, soft = check_workload(d, sandbox)
            ours = (d["metadata"].get("labels") or {}).get("app.kubernetes.io/part-of") == "llm-observability-lab"
            errs += hard + (soft if ours else [])
            warns += [] if ours else soft
        if sandbox and d["kind"] in CLUSTER_SCOPED:
            errs.append(f'{d["kind"]}/{d["metadata"]["name"]}: cluster-scoped object not allowed in the Sandbox')
    policies = [d for d in docs if d["kind"] == "NetworkPolicy"]
    if policies and not any(p["spec"].get("podSelector") == {} and set(p["spec"].get("policyTypes", []))
                            == {"Ingress", "Egress"} for p in policies):
        errs.append("no default-deny NetworkPolicy found")
    print(f"{root}: {len(docs)} objects, {sum(d['kind'] in WORKLOADS for d in docs)} workloads, "
          f"{len(policies)} network policies, {len(errs)} violations, {len(warns)} upstream warnings")
    for w in warns:
        print(f"  warn {w}")
    return [f"{root}: {e}" for e in errs]


def main(paths: list[str]) -> int:
    errs = [e for p in paths for e in check_dir(Path(p))]
    for e in errs:
        print(f"  FAIL {e}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["out"]))
