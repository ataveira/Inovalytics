# Licenças das dependências

Registro exigido pelo CLAUDE.md. Toda dependência declarada em `pyproject.toml` e
`frontend/package.json` deve aparecer na tabela (nome, versão verificada, licença SPDX, URL da fonte).
A verificação é automática: `make licencas` (parte do `make check`) roda
`python -m inovalytics.tools.license_check` (verificador, ADR-004). Exceções à política ficam em
`docs/licencas-excecoes.toml`; as dependências transitivas Python ficam em
`docs/licencas-transitivas.md` (gerado por `make licencas-sync`). Nenhuma dependência da
Ultralytics (AGPL-3.0).

## Política de licenças

- Permitidas: MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, ISC, PSF-2.0, Python-2.0, 0BSD,
  Unlicense, CC0-1.0, PostgreSQL, Zlib, BlueOak-1.0.0.
- MPL-2.0 só por exceção registrada em `docs/licencas-excecoes.toml`.
- Proibidas, sem exceção possível: AGPL, GPL, LGPL, SSPL, BUSL, non-commercial, proprietárias e
  tudo que venha da Ultralytics.
- Licença ausente, desconhecida ou ilegível falha o gate. Python-2.0 e BlueOak-1.0.0 foram
  aprovadas pelo humano em 2026-10-28 (ADR-004). O código (`license_policy.py`) é a fonte executável.

## Dependências diretas


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
| python-multipart | 0.0.32 | Apache-2.0 | https://github.com/Kludex/python-multipart |
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

## Dependências transitivas

A lista completa das transitivas Python (nome, licença SPDX, grupo e quem exige) está em
`docs/licencas-transitivas.md`, gerada a partir dos metadados dos pacotes instalados. Para npm, o
`frontend/package-lock.json` versionado é a árvore resolvida, com a licença de cada entrada.

Exceção de copyleft fraco: certifi (MPL-2.0, nível de arquivo, sem modificação). Verificado com
`pip show certifi`: `Required-by: httpcore, httpx`; botocore não o requer (usa urllib3). Como httpx
está só no extra `dev`, certifi NÃO entra no runtime/imagem da API, apenas no ambiente de
desenvolvimento e testes. Exceção APROVADA pelo humano em 2026-10-28 (ADR-002), registrada em
`docs/licencas-excecoes.toml`. Nenhuma GPL/AGPL/SSPL.

Exceção de copyleft fraco: pathspec 1.1.1 (MPL-2.0, nível de arquivo). Verificado com
`pip show pathspec`: `Required-by: mypy`; o METADATA traz o classifier `License :: OSI Approved ::
Mozilla Public License 2.0 (MPL 2.0)`. Dependência transitiva só do grupo `dev`, sem modificação,
sem redistribuição e fora da imagem da API. Exceção APROVADA pelo humano em 2026-10-28 (ADR-004),
restrita a pacote, versão `==1.1.1` e grupo `dev` em `docs/licencas-excecoes.toml`.

Observação: a licença do próprio Inovalytics é uma decisão pendente (ver `docs/spec.md`).

## GitHub Actions

Somente actions oficiais (organização `actions`), fixadas por versão major. As versões foram
confirmadas em 2026-10-28: o CI executou com elas e passou; ver ADR-003.

| Nome | Versão | Licença (SPDX) | URL |
|------|--------|----------------|-----|
| actions/checkout | v5 | MIT | https://github.com/actions/checkout |
| actions/setup-python | v6 | MIT | https://github.com/actions/setup-python |
| actions/setup-node | v6 | MIT | https://github.com/actions/setup-node |
| actions/upload-artifact | v5 | MIT | https://github.com/actions/upload-artifact |
