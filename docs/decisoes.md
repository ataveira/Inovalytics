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

- Status: aprovada pelo humano em 2026-10-28 (SeaweedFS, asyncpg e exceção de licença do certifi).
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
- Exceção de licença certifi (MPL-2.0, copyleft fraco em nível de arquivo): dependência transitiva
  de httpx/httpcore (extra `dev`), não modificada e fora do runtime da API. Status: APROVADA pelo
  humano em 2026-10-28.
- Exposição de rede: a porta da API é publicada por padrão só em `127.0.0.1` (`API_BIND`, padrão
  `127.0.0.1`; decisão do humano em 2026-10-28). Para expor à rede, defina `API_BIND=0.0.0.0` no `.env`.
  Postgres e storage seguem sem porta publicada.
- Configuração: as settings ficaram em `inovalytics/api/config.py` (e não em `inovalytics/config.py`
  como no backlog). `POSTGRES_PASSWORD`, `S3_ACCESS_KEY` e `S3_SECRET_KEY` são obrigatórias.
- Dívida: a criação do bucket dentro de `/health/ready` é efeito colateral num endpoint de leitura;
  mover para o startup da API ou para a F1-02.

## ADR-003: CI no GitHub Actions (F0-03)

- Status: proposta, para revisão.
- Makefile como fonte da verdade: o CI só instala dependências e chama `make check` (gate) e
  `make compose-smoke` (smoke). Nenhuma lógica de qualidade fica duplicada no YAML. Isso mantém
  portabilidade: trocar de provedor de CI (GitLab, Gitea, Jenkins) exige reescrever só a instalação.
- Workflow `.github/workflows/ci.yml`: gatilhos `push` (`main`, `feat/**`), `pull_request` e
  `workflow_dispatch`; `permissions: contents: read`; concorrência com `cancel-in-progress`; runner
  `ubuntu-24.04`; `persist-credentials: false`; sem segredos e sem `pull_request_target`.
- Job `gate`: matriz Python 3.11 e 3.12, Node 22, `pip install -e ".[dev]"`, `npm ci`, `make check`.
  `engines.node >= 20` declarado em `frontend/package.json`.
- Job `compose-smoke`: independente do gate. Roda em push para `main`, `workflow_dispatch` e em PR
  apenas se mudar `docker-compose.yml`, `docker/**`, `.env.example` ou `backend/src/**` (filtro por
  `git diff` em passo de shell, sem actions de terceiros). A primeira execução também confirma as tags
  de imagem registradas em `docs/licencas.md`.
- O smoke só vira check obrigatório na proteção de branch após 3 execuções verdes seguidas.
- Actions: apenas oficiais (`actions/checkout@v5`, `actions/setup-python@v6`, `actions/setup-node@v6`),
  fixadas por versão MAJOR e não por SHA. A escolha entre major e SHA de 40 hex é decisão do humano
  (SHA é mais seguro contra mudança de tag; major dá menos manutenção). As versões major foram
  confirmadas em 2026-10-28: as execuções do CI resolveram e passaram.
  O teste estático aceita as duas formas.
- Consequência: `test_no_ultralytics` passa a varrer também `.github/`.
- Concorrência: `cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}`. Execuções em `main` não
  são canceladas (o smoke de `main` conta para a regra das 3 execuções verdes); nas demais refs, a
  execução antiga é cancelada.
- Aceito: push e PR da mesma branch disparam execuções duplicadas (grupos de concorrência distintos,
  pois `github.ref` difere). Custo tolerado pela simplicidade.
- Aceito, a revisar depois: o filtro do smoke não inclui `pyproject.toml` nem
  `backend/tests/test_compose_smoke.py`; mudanças só neles não disparam o smoke em PR.
- Confirmado em 2026-10-28 no GitHub: Node 22 e as versões major das actions funcionam; o job `gate`
  passou em Python 3.11 e 3.12 (cerca de 40 s cada) e o `compose-smoke` manual passou (cerca de 2 min,
  incluindo o pull das imagens). Ainda falta ele ficar verde em 3 execuções seguidas para virar check
  obrigatório, e o erro de lint proposital (critério 17) não foi exercitado.
