SHELL := /bin/bash

# A pre-push hook runs with GIT_DIR naming this repository; nothing here runs
# git on the host, and this keeps it that way should something start to.
unexport GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR \
         GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_PREFIX

.PHONY: help ci-local ci-local-deep ci-remote test lint check image e2e fmt install-hooks

DOCKER      := DOCKER_BUILDKIT=1 docker
PY_VERSION  := 3.12
CHECK       := meridian-oanda-check
IMAGE       ?= oanda:local
# The SDK pyproject.toml pins, and its base image. Where the sibling
# meridian-python checkout carries exactly that version -- one not yet
# published, being tried -- it is built from there (SDK_REPO). Otherwise both
# come from PyPI and ghcr.io.
SDK_VERSION := $(shell sed -n 's/.*"open-meridian==\([0-9][0-9.]*\)".*/\1/p' pyproject.toml)
SDK_REPO    ?= $(if $(shell grep -s '^version = "$(SDK_VERSION)"$$' ../meridian-python/pyproject.toml),../meridian-python,)
SDK_CONTEXT := $(if $(SDK_REPO),--build-context sdk=$(SDK_REPO),)
BASE        ?= $(if $(SDK_REPO),plugin-python:$(SDK_VERSION)-local,ghcr.io/open-meridian/plugin-python:$(SDK_VERSION))
PLUGIN_CHECK := meridian-oanda-plugin-check
# The meridian check.yaml holds the plugin to, read from there so the two
# cannot drift.
MERIDIAN_VERSION := $(shell sed -n 's/^ *MERIDIAN_VERSION: *\([0-9][0-9.]*\).*/\1/p' .github/workflows/check.yaml)
# A meridian built locally (a directory holding its binary), for a release
# not yet published: `make check MERIDIAN_BIN=../cli-build`.
MERIDIAN_BIN ?=
CLI_CONTEXT := $(if $(MERIDIAN_BIN),--build-context cli=$(MERIDIAN_BIN),)

# The released runtime `make e2e` proves the plugin against, by tag and
# digest, and core's plugin harness published with it, by the same commit's
# tag and its digest: core 50d6f0a, contract v23, as published (chart 0.1.303).
# Moved together by a deliberate commit. `make e2e RUNTIME_IMAGE=...:latest
# HARNESS_IMAGE=...:latest` tries a newer core; e2e-latest.yaml does that
# weekly.
# Core a5da59a (merged as 6d346b3, the same tree), contract v27, as published with chart 0.1.310.
RUNTIME_IMAGE ?= ghcr.io/open-meridian/meridian-runtime:a5da59a@sha256:05694f1ca4d642def4e2572c78a56c3ce3c43d060f4bac2983e19283e98910de
HARNESS_IMAGE ?= ghcr.io/open-meridian/meridian-harness:a5da59a@sha256:74a1e5f08308e26571141b24548082280fed8519976c08b32cd8a4b34fc8e08c
# Its roles as pyproject.toml declares them, so the harness launches it as
# `meridian plugin upload` would.
ROLES := $(shell sed -n 's/^roles *= *\[\(.*\)\]/\1/p' pyproject.toml | tr -d ' ')
# The harness's plugins: this one as instance oanda, given the harness's
# archive beside its storage, and the desk the order path places from
# (e2e/desk.py: portfolio, oms and ems), run from this plugin's image.
# The declaration [tool.meridian] names (contract v25): `make e2e` prints it
# from the image under test, as `meridian plugin upload` reads it, and the
# harness holds the plugin's registration to it (its README, `declaration`).
# Each stand-in's the same, its directory mounted as it runs.
DECLARATION_REF := $(shell sed -n 's/^declaration *= *"\(.*\)"/\1/p' pyproject.toml)
DECLARE := docker run --rm --network none --entrypoint meridian-declaration
PLUGINS := [{"instance": "oanda", "image": "$(IMAGE)", "roles": [$(ROLES)], "archive": true, "declaration": "declaration.json"}, {"instance": "desk", "image": "$(IMAGE)", "roles": ["portfolio", "oms", "ems", "reporting"], "declaration": "desk.json"}]
# The plugin harness copied out of HARNESS_IMAGE, as its own project, with
# the plugins it wrote and e2e/plugin.yaml's overrides.
E2E_COMPOSE := docker compose -p oanda-e2e -f .e2e/harness/compose.yaml \
           -f .e2e/harness/plugins.yaml -f e2e/plugin.yaml
E2E     := MERIDIAN_RUNTIME_IMAGE=$(RUNTIME_IMAGE) E2E_DIR=$(CURDIR)/e2e $(E2E_COMPOSE)

help:
	@echo "  make ci-local       every gate: lint, tests, plugin check, the plugin's image, and e2e (the pre-push gate)"
	@echo "  make ci-remote      what ci.yaml runs: lint and tests"
	@echo "  make test           the tests, in a container"
	@echo "  make lint           ruff and mypy, strict"
	@echo "  make check          what check.yaml runs: meridian plugin check --verified --run-tests, in a container"
	@echo "  make image          build the plugin's image, as upload would, and check it"
	@echo "  make e2e            the plugin, on the synthetic OANDA, on the plugin harness of the runtime it pins"
	@echo "  make fmt            apply the formatter"
	@echo "  make install-hooks  point git at hooks/ so push fires ci-local"

# Local green is the completion signal; CI is confirmation. Every CI job is a
# target reachable from here.
ci-local: ci-remote check image e2e
	@echo
	@echo "ci-local: GREEN"

ci-local-deep: ci-local

ci-remote: lint test
	@echo
	@echo "ci-remote: GREEN"

lint:
	@$(DOCKER) build $(SDK_CONTEXT) -f Dockerfile.check --target lint . >/dev/null 2>&1 \
		|| { echo "lint FAILED; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build $(SDK_CONTEXT) -f Dockerfile.check --target lint --progress=plain ." >&2; exit 1; }
	@echo "lint OK: ruff and mypy (strict) clean"

test:
	@$(DOCKER) build $(SDK_CONTEXT) -f Dockerfile.check --target test -t $(CHECK) . >/dev/null 2>&1 \
		|| { echo "test FAILED to build; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build $(SDK_CONTEXT) -f Dockerfile.check --target test --progress=plain ." >&2; exit 1; }
	@docker run --rm $(CHECK) python -m pytest -q -rs >.test.log 2>&1 \
		|| { echo "test FAILED. The last 40 lines, and the whole of it in .test.log:" >&2; \
		     tail -40 .test.log >&2; exit 1; }
	@echo "test OK: $$(tail -1 .test.log)"

# check.yaml's job, in a container: the meridian it pins, installed as it is
# there, holds the plugin to the framework's rules and runs its tests. The
# repository is mounted read-only, and the check writes nothing to it.
check:
	@[ -n "$(MERIDIAN_VERSION)" ] \
		|| { echo "check FAILED: .github/workflows/check.yaml pins no MERIDIAN_VERSION" >&2; exit 1; }
	@$(DOCKER) build $(SDK_CONTEXT) -f Dockerfile.check --target check $(CLI_CONTEXT) --build-arg MERIDIAN_VERSION=$(MERIDIAN_VERSION) \
		-t $(PLUGIN_CHECK) . >/dev/null 2>&1 \
		|| { echo "check FAILED to build; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build $(SDK_CONTEXT) -f Dockerfile.check --target check $(CLI_CONTEXT) --build-arg MERIDIAN_VERSION=$(MERIDIAN_VERSION) --progress=plain ." >&2; exit 1; }
	@out="$$(docker run --rm -v "$(CURDIR)":/w:ro $(PLUGIN_CHECK) meridian plugin check --verified --run-tests 2>&1)" \
		|| { echo "check FAILED:" >&2; echo "$$out" >&2; exit 1; }
	@echo "check OK: meridian $(MERIDIAN_VERSION) plugin check --verified --run-tests, every rule holds"

# The image `meridian plugin upload` builds from this Dockerfile, checked for
# what a plugin's image must be: it starts as 65532, imports itself, and
# carries nothing of this repository but the package.
image:
	@if [ -n "$(SDK_REPO)" ]; then \
		$(MAKE) --no-print-directory -C "$(SDK_REPO)" base-image BASE_IMAGE=$(BASE) >/dev/null \
			|| { echo "image FAILED: the base for open-meridian $(SDK_VERSION) was not built from $(SDK_REPO)" >&2; exit 1; }; \
	fi
	@$(DOCKER) build --build-arg BASE=$(BASE) -t $(IMAGE) . >/dev/null 2>&1 \
		|| { echo "image FAILED; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build --build-arg BASE=$(BASE) --progress=plain ." >&2; exit 1; }
	@[ "$$(docker inspect -f '{{.Config.User}}' $(IMAGE))" = "65532" ] \
		|| { echo "image FAILED: it does not run as 65532" >&2; exit 1; }
	@docker run --rm --entrypoint python $(IMAGE) -c "import meridian, oanda.__main__, oanda.page" \
		|| { echo "image FAILED: it does not import the SDK and itself" >&2; exit 1; }
	@[ "$$(docker run --rm --entrypoint sh $(IMAGE) -c 'ls -A /plugin')" = "docs" ] \
		|| { echo "image FAILED: /plugin holds files beyond docs/; only the installed package and its documentation belong" >&2; exit 1; }
	@echo "image OK: $(IMAGE) on $(BASE), $$(docker image inspect -f '{{.Size}}' $(IMAGE) | awk '{printf "%.0f MB", $$1/1e6}')"

# The plugin, in sample data and with no token, on the plugin harness
# published with the runtime it pins: e2e/custody.py chooses the synthetic
# OANDA account on Connections from those its token reaches (preselected,
# confirmed by the admin; the v1 account unusable), links it through the
# plugin's own Account links form and holds the
# street's statement and activity to e2e/expected.street and
# e2e/expected.activity, a row's raw record followed, and a new container's
# backfill recording nothing twice; then e2e/orders.py runs the order path
# with the desk (e2e/desk.py). SIMULATED ONLY: the synthetic OANDA, never
# OANDA's practice or live hosts. Its own project and no published port;
# nothing outlives it.
e2e: image
	@[ -n '$(ROLES)' ] || { echo "e2e FAILED: pyproject.toml declares no roles" >&2; exit 1; }
	@rm -rf .e2e && mkdir -p .e2e
	@for image in $(RUNTIME_IMAGE) $(HARNESS_IMAGE); do \
		case "$$image" in *@sha256:*) ;; *) docker pull -q "$$image" >/dev/null 2>&1 || true;; esac; \
	done
	@started=$$(date +%s); \
	trap '$(E2E) --profile runner down -v --remove-orphans >>.e2e/components.log 2>&1; rm -rf .e2e/harness' EXIT; \
	fail() { grep -h '^harness .* FAILED' .e2e/runner.log 2>/dev/null | tail -1 >&2; \
		echo "e2e FAILED: $$1; the components' logs are in .e2e/components.log, the runner's in .e2e/runner.log" >&2; \
		$(E2E) logs --no-color >>.e2e/components.log 2>&1; exit 1; }; \
	id="$$(docker create $(HARNESS_IMAGE) none 2>>.e2e/components.log)" \
		&& docker cp "$$id:/harness" .e2e/harness >/dev/null \
		&& docker rm "$$id" >/dev/null \
		|| { echo "e2e FAILED: no plugin harness at /harness in $(HARNESS_IMAGE)" >&2; trap - EXIT; exit 1; }; \
	$(DECLARE) $(IMAGE) $(DECLARATION_REF) >.e2e/harness/declaration.json 2>>.e2e/components.log \
		|| { echo "e2e FAILED: meridian-declaration did not print $(DECLARATION_REF) (declaration.json)" >&2; exit 1; }; \
	$(DECLARE) -v "$(CURDIR)/e2e":/e2e:ro -e PYTHONPATH=/e2e $(IMAGE) desk:DECLARATION >.e2e/harness/desk.json 2>>.e2e/components.log \
		|| { echo "e2e FAILED: meridian-declaration did not print desk:DECLARATION (desk.json)" >&2; exit 1; }; \
	printf '%s\n' '$(PLUGINS)' >.e2e/plugins.json; \
	docker run --rm -i -v "$(CURDIR)/.e2e/harness":/harness:ro python:3.12-alpine \
		python /harness/harness.py compose <.e2e/plugins.json >.e2e/harness/plugins.yaml 2>>.e2e/components.log \
		|| { echo "e2e FAILED: the harness did not write its plugins from .e2e/plugins.json" >&2; exit 1; }; \
	$(E2E) --profile runner down -v --remove-orphans >>.e2e/components.log 2>&1; \
	$(E2E) up -d >>.e2e/components.log 2>&1 || fail "the harness did not start"; \
	custody="$$(MERIDIAN_RUNTIME_IMAGE=$(RUNTIME_IMAGE) E2E_DIR=$(CURDIR)/e2e python3 e2e/custody.py $(E2E_COMPOSE))" \
		|| fail "the custody steps (e2e/custody.py) failed: $$(tail -c 600 .e2e/custody.log)"; \
	orders="$$(MERIDIAN_RUNTIME_IMAGE=$(RUNTIME_IMAGE) E2E_DIR=$(CURDIR)/e2e python3 e2e/orders.py $(E2E_COMPOSE))" \
		|| fail "the order path (e2e/orders.py) failed: $$(tail -c 600 .e2e/orders.log)"; \
	echo "e2e OK in $$(( $$(date +%s) - started ))s on $(RUNTIME_IMAGE) and its harness: $$custody; then the order path, OANDA holding connectivity beside custody and the desk placing on it: $$orders"

# Applied in a container and written back, because the host has no toolchain.
fmt:
	@docker run --rm -v "$(CURDIR)":/w -w /w python:$(PY_VERSION)-slim \
		sh -c 'pip install -q ruff >/dev/null 2>&1; python -m ruff check --fix src tests >/dev/null; python -m ruff format src tests'
	@echo "fmt: applied"

install-hooks:
	@git config core.hooksPath hooks
	@echo "hooks installed: git push now runs 'make ci-local' first"
