#!/usr/bin/env bash
# Check that we are logged in to the OpenShift Developer Sandbox and can deploy.
set -euo pipefail
command -v oc >/dev/null || { echo "missing: oc (https://mirror.openshift.com/pub/openshift-v4/clients/ocp/)" >&2; exit 1; }
oc whoami >/dev/null 2>&1 || { echo "not logged in. Copy the login command from the Sandbox console (top right > Copy login command)." >&2; exit 1; }
NS="$(oc project -q)"
echo "user: $(oc whoami)  namespace: ${NS}"
for verb_res in "create deployments" "create networkpolicies" "create routes" "create secrets" "create persistentvolumeclaims"; do
  # shellcheck disable=SC2086
  if oc auth can-i ${verb_res} -n "${NS}" >/dev/null; then echo "  ok   ${verb_res}"; else echo "  FAIL ${verb_res}"; exit 1; fi
done
oc get resourcequota -n "${NS}" -o custom-columns=NAME:.metadata.name,HARD:.status.hard --no-headers 2>/dev/null | head -5 || true
