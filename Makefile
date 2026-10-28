PYTHON ?= python
NPM_FRONT = npm --prefix frontend

.PHONY: test lint check lint-backend lint-frontend typecheck test-frontend frontend-deps

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
	@! grep -rniE "(^|\s)(from|import)\s+ultralytics" --include=*.py --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=.git . || (echo "ERRO: uso de ultralytics proibido (AGPL)"; exit 1)
	$(MAKE) lint-backend
	$(MAKE) typecheck
	$(MAKE) test
	$(MAKE) lint-frontend
	$(MAKE) test-frontend
