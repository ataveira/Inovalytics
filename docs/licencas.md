# Licenças das dependências

Registro exigido pelo CLAUDE.md. Toda dependência declarada em `pyproject.toml` e
`frontend/package.json` deve aparecer na tabela (nome, versão verificada, licença SPDX, URL da fonte).
A automação da verificação fica para a F0-04. Nenhuma dependência da Ultralytics (AGPL-3.0).

| Nome | Versão | Licença (SPDX) | URL |
|------|--------|----------------|-----|
| pytest | 9.2.0 | MIT | https://github.com/pytest-dev/pytest |
| ruff | 0.17.3 | MIT | https://github.com/astral-sh/ruff |
| mypy | 2.4.0 | MIT | https://github.com/python/mypy |
| vitest | 3.2.7 | MIT | https://github.com/vitest-dev/vitest |
| eslint | 9.39.5 | MIT | https://github.com/eslint/eslint |
| @eslint/js | 9.39.5 | MIT | https://github.com/eslint/eslint |
| typescript | 5.9.3 | Apache-2.0 | https://github.com/microsoft/TypeScript |
| typescript-eslint | 8.72.1 | MIT | https://github.com/typescript-eslint/typescript-eslint |
| fastapi | 0.162.0 | MIT | https://github.com/fastapi/fastapi |
| uvicorn | 0.54.0 | BSD-3-Clause | https://github.com/Kludex/uvicorn |
| asyncpg | 0.32.0 | Apache-2.0 | https://github.com/MagicStack/asyncpg |
| boto3 | 1.43.120 | Apache-2.0 | https://github.com/boto/boto3 |
| httpx | 0.28.1 | BSD-3-Clause | https://github.com/encode/httpx |
| pyyaml | 6.0.3 | MIT | https://github.com/yaml/pyyaml |
| types-PyYAML | 6.0.12.20260906 | Apache-2.0 | https://github.com/python/typeshed |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | https://github.com/pypa/packaging |

## Imagens de contêiner

| Nome | Versão | Licença (SPDX) | URL |
|------|--------|----------------|-----|
| postgres (imagem oficial) | 17.6 | PostgreSQL | https://github.com/docker-library/postgres |
| chrislusf/seaweedfs | 3.97 | Apache-2.0 | https://github.com/seaweedfs/seaweedfs |
| python (imagem base, slim-bookworm) | 3.12.11 | PSF-2.0 | https://github.com/docker-library/python |

As imagens `postgres` e `python` incluem pacotes Debian com licenças diversas (inclusive GPL),
usados sem modificação como distribuição oficial. SeaweedFS roda sem modificações como
serviço separado, acessado só pela rede (API S3). Tags confirmadas em 2026-10-28: o `compose-smoke` manual (workflow_dispatch, run 40735240168) fez o
pull e subiu a stack com sucesso no GitHub Actions.

## Dependências transitivas relevantes (verificadas via metadados do pacote instalado)

starlette 1.10.0 (BSD-3-Clause), pydantic 2.14.1 (MIT), pydantic-core 2.50.1 (MIT),
anyio 4.15.1 (MIT), click 8.5.0 (BSD-3-Clause), h11 0.16.0 (MIT), httpcore 1.0.9 (BSD-3-Clause),
botocore 1.43.120 (Apache-2.0), s3transfer 0.19.3 (Apache-2.0), jmespath 1.1.0 (MIT),
urllib3 2.8.0 (MIT), python-dateutil 2.9.0 (BSD-3-Clause ou Apache-2.0), six 1.17.0 (MIT),
idna 3.20 (BSD-3-Clause), typing-extensions 4.16.0 (PSF-2.0), annotated-types (MIT),
opentelemetry-api 1.45.1 (Apache-2.0). Exceção de copyleft fraco: certifi 2026.10.23 (MPL-2.0,
nível de arquivo, sem modificação). Verificado com `pip show certifi`: `Required-by: httpcore, httpx`;
botocore 1.43.120 não o requer (usa urllib3). Como httpx está só no extra `dev`, certifi NÃO entra
no runtime/imagem da API, apenas no ambiente de desenvolvimento e testes. Exceção APROVADA pelo
humano em 2026-10-28 (ver ADR-002). Nenhuma GPL/AGPL/SSPL.

Observação: a licença do próprio Inovalytics é uma decisão pendente (ver `docs/spec.md`).

## GitHub Actions

Somente actions oficiais (organização `actions`), fixadas por versão major. As versões foram
confirmadas em 2026-10-28: o CI executou com elas e passou; ver ADR-003.

| Nome | Versão | Licença (SPDX) | URL |
|------|--------|----------------|-----|
| actions/checkout | v5 | MIT | https://github.com/actions/checkout |
| actions/setup-python | v6 | MIT | https://github.com/actions/setup-python |
| actions/setup-node | v6 | MIT | https://github.com/actions/setup-node |
