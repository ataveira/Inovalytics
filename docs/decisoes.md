# Decisões (ADR curtos)

## ADR-001: Layout do monorepo e ferramentas (F0-01)

- Status: proposta, para revisão.
- Layout: `backend/src/inovalytics/` (pacote Python, layout `src`), `backend/tests/`,
  `frontend/` (vitest + eslint; React entra em tarefa futura), `docker/` (Compose na F0-02),
  `docs/`. `pyproject.toml` e `Makefile` na raiz.
- Ferramentas: pytest, ruff (lint e format), mypy `strict` em `backend/src`; vitest e eslint no front.
  Todas permissivas (MIT/Apache-2.0), registradas em `docs/licencas.md`.
- `make check` roda, em ordem: bloqueio de `ultralytics`, ruff, mypy, pytest, eslint, vitest.
- A licença do projeto não é declarada no `pyproject.toml` até a decisão pendente da spec.
- Configurações escritas do zero; nada copiado de terceiros.

## ADR-002: Armazenamento S3 (SeaweedFS) e driver PostgreSQL (asyncpg) (F0-02)

- Status: aprovada pelo humano.
- Armazenamento: SeaweedFS (Apache-2.0, gateway S3) como serviço `storage`, imagem com tag fixa.
  A API usa boto3 e as variáveis `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`;
  o servidor é intercambiável por configuração.
- Por que não MinIO: servidor AGPL-3.0, em conflito com a Regra nº 1 (somente componentes permissivos);
  quem distribui o instalador on-premise assumiria obrigações AGPL. Garage também é AGPL-3.0.
- Driver PostgreSQL: asyncpg (Apache-2.0), assíncrono, combinando com FastAPI.
- Por que não psycopg: LGPL-3.0 (copyleft fraco), em tensão com a decisão pendente de código aberto
  ou fechado do projeto.
- Consequências: ORM e migrações ficam para a F1-01. O `/health/ready` cria o bucket se ele não existir
  (infraestrutura, idempotente). Autenticação do SeaweedFS S3 via `AWS_ACCESS_KEY_ID` e
  `AWS_SECRET_ACCESS_KEY` no contêiner `storage`, preenchidas com `S3_ACCESS_KEY`/`S3_SECRET_KEY`.
