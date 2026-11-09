# LLM Observability Lab - one command per job.
#   make up                      local k3d cluster, build image, deploy everything
#   make up ENV=sandbox          OpenShift Developer Sandbox (oc login first)
#   make up DISABLE="loki tempo" skip components
SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

ENV      ?= k3d
CLUSTER  ?= llm-lab
IMAGE    ?= ghcr.io/mistral-valaise/llm-observability-lab/ai-gateway
TAG      ?= dev
DISABLE  ?=
APP_DIR  := apps/ai-gateway

ifeq ($(ENV),sandbox)
  KUBECTL   := oc
  NAMESPACE ?= $(shell oc project -q 2>/dev/null)
else
  KUBECTL   := kubectl
  NAMESPACE ?= llm-lab
endif
export NAMESPACE

comma := ,
empty :=
space := $(empty) $(empty)
SET_FLAGS := $(if $(strip $(DISABLE)),--state-values-set $(subst $(space),$(comma),$(foreach c,$(DISABLE),components.$(c)=false)))
HELMFILE  := helmfile -e $(ENV) $(SET_FLAGS)

.PHONY: help
help: ## Show targets
	@grep -hE '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-18s\033[0m %s\n",$$1,$$2}'

## ---- Deploy ---------------------------------------------------------------
.PHONY: up
up: ## Deploy the whole demo to ENV (k3d | sandbox) with one command
ifeq ($(ENV),k3d)
	@NAMESPACE=$(NAMESPACE) CLUSTER=$(CLUSTER) scripts/k3d-up.sh
	@$(MAKE) --no-print-directory image-import
else
	@scripts/sandbox-preflight.sh
endif
	$(HELMFILE) apply --suppress-diff
	@$(MAKE) --no-print-directory urls

.PHONY: diff
diff: ## Show what `up` would change
	$(HELMFILE) diff

.PHONY: template
template: ## Render all manifests into out/<env>/ (no cluster needed)
	@rm -rf out/$(ENV) && mkdir -p out/$(ENV)
	NAMESPACE=$${NAMESPACE:-llm-lab} $(HELMFILE) template --skip-deps --output-dir out/$(ENV) --output-dir-template '{{ .OutputDir }}/{{ .Release.Name }}'

.PHONY: down
down: ## Remove all releases from ENV (k3d: also deletes the cluster)
	-$(HELMFILE) destroy
ifeq ($(ENV),k3d)
	-k3d cluster delete $(CLUSTER)
endif

## ---- Build ----------------------------------------------------------------
.PHONY: image
image: ## Build the gateway image
	docker build -t $(IMAGE):$(TAG) $(APP_DIR)

.PHONY: image-import
image-import: image ## Build and load the image into k3d
	k3d image import -c $(CLUSTER) $(IMAGE):$(TAG)

## ---- Quality --------------------------------------------------------------
.PHONY: test
test: ## Unit tests
	cd $(APP_DIR) && python -m pytest -q

.PHONY: lint
lint: ## Ruff, helm lint, rendered-manifest validation
	cd $(APP_DIR) && ruff check . && ruff format --check .
	helm lint charts/ai-gateway charts/lab-platform
	helm lint charts/ai-gateway --set openshift=true
	python3 scripts/gen_dashboard.py >/dev/null && git diff --exit-code charts/lab-platform/dashboards

.PHONY: smoke
smoke: ## End-to-end check against the running ENV
	KUBECTL=$(KUBECTL) NAMESPACE=$(NAMESPACE) scripts/smoke-test.sh

## ---- Demo -----------------------------------------------------------------
.PHONY: chaos
chaos: ## Inject 25 % upstream errors and 2 s latency (watch the burn-rate alert fire)
	@$(MAKE) --no-print-directory _chaos ERR=0.25 LAT=2000

.PHONY: calm
calm: ## Back to normal (1 % errors, 350 ms)
	@$(MAKE) --no-print-directory _chaos ERR=0.01 LAT=350

.PHONY: _chaos
_chaos:
	@ADMIN=$$($(KUBECTL) -n $(NAMESPACE) get secret ai-gateway-keys -o jsonpath='{.data.admin-key}' | base64 -d); \
	$(KUBECTL) -n $(NAMESPACE) exec deploy/ai-gateway -- python -c "import json,sys,urllib.request as u; \
	r=u.Request('http://localhost:8080/admin/chaos', data=json.dumps({'error_rate':$(ERR),'latency_ms':$(LAT)}).encode(), \
	headers={'content-type':'application/json','x-admin-key':sys.argv[1]}); print(u.urlopen(r).read().decode())" "$$ADMIN"

.PHONY: keys
keys: ## Print the tenant API keys
	@$(KUBECTL) -n $(NAMESPACE) get secret ai-gateway-keys -o jsonpath='{.data.api-keys}' | base64 -d; echo

.PHONY: grafana-password
grafana-password: ## Print the Grafana admin password (user: admin)
	@$(KUBECTL) -n $(NAMESPACE) get secret grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo

.PHONY: urls
urls: ## Show where to open the demo
ifeq ($(ENV),k3d)
	@echo "Grafana : http://grafana.localtest.me:8080  (user admin, password: make grafana-password)"
	@echo "Gateway : http://gateway.localtest.me:8080/v1/chat/completions"
else
	@oc get routes -n $(NAMESPACE) -o custom-columns=NAME:.metadata.name,URL:.spec.host --no-headers | awk '{print $$1": https://"$$2}'
endif
