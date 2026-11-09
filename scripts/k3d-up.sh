#!/usr/bin/env bash
# Create (or reuse) a local k3d cluster with Traefik on http://*.localtest.me:8080.
set -euo pipefail
CLUSTER="${CLUSTER:-llm-lab}"
for bin in k3d kubectl; do command -v "$bin" >/dev/null || { echo "missing: $bin" >&2; exit 1; }; done

if k3d cluster list -o json | grep -q "\"name\":\"${CLUSTER}\""; then
  echo "k3d cluster ${CLUSTER} exists, reusing it"
  k3d cluster start "${CLUSTER}" >/dev/null
else
  k3d cluster create "${CLUSTER}" \
    --agents 1 \
    --port "8080:80@loadbalancer" \
    --k3s-arg "--disable=metrics-server@server:0" \
    --wait
fi
kubectl config use-context "k3d-${CLUSTER}" >/dev/null
kubectl create namespace "${NAMESPACE:-llm-lab}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
# Enforce the restricted Pod Security Standard on the namespace (warn + audit + enforce).
kubectl label namespace "${NAMESPACE:-llm-lab}" --overwrite \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/warn=restricted \
  pod-security.kubernetes.io/audit=restricted >/dev/null
echo "cluster k3d-${CLUSTER} ready, namespace ${NAMESPACE:-llm-lab} enforces PSS restricted"
