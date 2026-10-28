.PHONY: test lint check

test:
	python -m pytest -q

lint:
	ruff check . && mypy backend

check: lint test
	@! grep -rniE "(^|\s)(from|import)\s+ultralytics" --include=*.py . || (echo "ERRO: uso de ultralytics proibido (AGPL)"; exit 1)
