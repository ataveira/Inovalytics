PYTHON ?= python
NPM_FRONT = npm --prefix frontend

.PHONY: compose-smoke test lint check lint-backend lint-frontend typecheck test-frontend frontend-deps

# Fora do `make check`: exige Docker (pula os testes se ausente).
compose-smoke:
	$(PYTHON) -m pytest -m docker backend/tests/test_compose_smoke.py

test:
	$(PYTHON) -m pytest

lint: lint-backend lint-frontend

lint-backend:
	$(PYTHON) -m ruff check backend
	$(PYTHON) -m ruff format --check backend

lint-frontend: frontend-deps
	$(NPM_FRONT) run lint

typecheck:
	$(PYTHON) -m mypy

test-frontend: frontend-deps
	$(NPM_FRONT) run test

frontend-deps:
	@test -d frontend/node_modules || $(NPM_FRONT) install --no-audit --no-fund

# Ordem: ruff, mypy, pytest, depois eslint e vitest do frontend.
check:
	@# Limitação: este grep é só a primeira barreira (apenas .py, apenas imports no início da linha;
	@# não vê Dockerfile, .toml, .json, imports dinâmicos). Cobertura completa: backend/tests/test_no_ultralytics.py.
	@! grep -rniE "(^|\s)(from|import)\s+ultralytics" --include=*.py --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=.git . || (echo "ERRO: uso de ultralytics proibido (AGPL)"; exit 1)
	$(MAKE) lint-backend
	$(MAKE) typecheck
	$(MAKE) test
	$(MAKE) lint-frontend
	$(MAKE) test-frontend
