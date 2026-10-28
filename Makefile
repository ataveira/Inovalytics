PYTHON ?= python
NPM_FRONT = npm --prefix frontend
# Só para `licencas-sync` (ex.: LICENCAS_ARGS=--prune). O alvo `licencas` não aceita argumentos.
LICENCAS_ARGS ?=

.PHONY: compose-smoke test lint check lint-backend lint-frontend typecheck test-frontend frontend-deps licencas licencas-sync migrate

# Fora do `make check`: exige Docker (pula os testes se ausente).
compose-smoke:
	$(PYTHON) -m pytest -m docker backend/tests/test_compose_smoke.py backend/tests/test_db_postgres_docker.py backend/tests/test_upload_docker.py

# Migrações do banco (F1-04b, ADR-006): comando separado, nunca no startup da API.
# Lê POSTGRES_* do ambiente. Para reverter (só dev/teste): python -m inovalytics.db.migrate down --steps N
migrate:
	$(PYTHON) -m inovalytics.db.migrate up

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

# Verificação de licenças (F0-04): offline, nunca escreve. Falha com código 1 se houver violação.
licencas:
	$(PYTHON) -m inovalytics.tools.license_check

# Atualiza docs/licencas-transitivas.md por união (nunca apaga; use --prune à mão). Rode também no
# Python 3.11 e no 3.12 e faça commit, pois os marcadores de ambiente mudam a árvore.
licencas-sync:
	$(PYTHON) -m inovalytics.tools.license_check --write $(LICENCAS_ARGS)

# Ordem: ruff, mypy, pytest, licenças, depois eslint e vitest do frontend.
check:
	@# Limitação: este grep é só a primeira barreira (apenas .py, apenas imports no início da linha;
	@# não vê Dockerfile, .toml, .json, imports dinâmicos). Cobertura completa: backend/tests/test_no_ultralytics.py.
	@! grep -rniE "(^|\s)(from|import)\s+ultralytics" --include=*.py --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=.git . || (echo "ERRO: uso de ultralytics proibido (AGPL)"; exit 1)
	$(MAKE) lint-backend
	$(MAKE) typecheck
	$(MAKE) test
	$(MAKE) licencas
	$(MAKE) lint-frontend
	$(MAKE) test-frontend
