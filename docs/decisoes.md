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
- Dívida: a criação do bucket dentro de `/health/ready` era efeito colateral num endpoint de leitura.
  RESOLVIDA na F0-05 (ver ADR-005); `/health/ready` é somente leitura.

## ADR-005: Criação do bucket S3 no startup da API (F0-05)

- Status: proposta (aguarda revisão).
- Decisão: `ensure_bucket` roda no lifespan do FastAPI; `check_s3` do `/health/ready` só faz
  `head_bucket`. O contrato HTTP de `/health` e `/health/ready` não mudou.
- Política: storage é obrigatório no startup; Postgres não. Se o bucket não puder ser
  verificado/criado após o retry, o startup falha (processo sai com código != 0). Motivos: o compose
  já tem `depends_on: storage: service_healthy` e `restart: unless-stopped` (falha rápida + reinício
  é a recuperação natural); evita estado meio-configurado em que uploads (F1-05) falham sem causa
  clara. O Postgres fica de fora porque a API deve continuar viva devolvendo 503 quando o `db` para
  (critério 13 da F0-02).
- Retry: `S3_STARTUP_TIMEOUT` (prazo total, padrão 30 s) e `S3_STARTUP_RETRIES` (tentativas,
  padrão 5), ambos positivos. Backoff 0,5 s dobrando até 5 s, limitado pelo prazo restante. Só erros
  transitórios (conexão, timeout, HTTP 5xx) repetem; credenciais ou permissão erradas falham na 1ª
  tentativa. `BucketAlreadyOwnedByYou`/`BucketAlreadyExists` contam como sucesso (corrida entre
  réplicas). O boto3 segue com `max_attempts=1`; o retry é laço próprio, sem biblioteca nova.
- Segredos: log e exceção citam só bucket, tipo da exceção e nº da tentativa.
- Testes: `create_app(..., bucket_initializer=...)`; com `checks` injetados e sem initializer o
  startup é no-op. Não há variável de produção para pular o startup. Os testes usam a fixture
  `no_external_network` (a `no_network` bloqueia `socket.socket` e quebra o event loop do asyncio).

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

## ADR-004: Verificação automática de licenças (F0-04)

- Status: proposta, para revisão. As decisões marcadas "humano" foram tomadas em 2026-10-28.
- Onde: `backend/src/inovalytics/tools/` (`license_policy.py` núcleo puro, `license_collect.py`
  coletores, `license_registry.py` registros, `license_check.py` CLI), chamado por `make licencas`
  e pelo `make check` (depois do pytest, antes do eslint). Offline: sem rede, `subprocess`, `npm`
  ou `pip`. Não é parecer jurídico.
- Política: permitidas MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, ISC, PSF-2.0, Python-2.0, 0BSD,
  Unlicense, CC0-1.0, PostgreSQL, Zlib, BlueOak-1.0.0; MPL-2.0 só por exceção; AGPL, GPL, LGPL,
  SSPL, BUSL, non-commercial, proprietárias e Ultralytics sempre falham, sem exceção. Falha fechada:
  licença ausente, desconhecida, ilegível ou fora da lista bloqueia.
- Decisão do humano (2026-10-28): Python-2.0 e BlueOak-1.0.0 entram na lista (permissivas; hoje só
  em dependências de dev do `package-lock.json`).
- Decisão do humano (2026-10-28): os classifiers genéricos "BSD License" e "Apache Software License"
  são aceitos como BSD-3-Clause e Apache-2.0, e a saída marca "normalizado de forma genérica" para
  auditoria. Nada além disso foi aprovado.
- Ordem das fontes (Python): `License-Expression` (PEP 639) decide sozinha; senão, o campo `License`
  (só se curto e de uma linha) somado aos classifiers `License ::`, e todas as licenças reconhecidas
  devem ser permitidas. npm: campo `license` de cada entrada do lock (`lockfileVersion` 2 ou 3;
  `resolved` fora do registro npm falha). Expressões SPDX: `OR` basta uma alternativa permitida,
  `AND` exige todas, `WITH` é desconhecido. Imagens e actions: linha registrada em
  `docs/licencas.md` (limite: não há metadado legível offline; a verificação é humana).
- Exceções em `docs/licencas-excecoes.toml`: presas a pacote, versões, grupo e licença declarada, com
  evidência, justificativa, aprovador, data e ADR; exceção inválida falha o gate mesmo sem uso.
  Hoje são duas: certifi (MPL-2.0, `dev`, ADR-002) e pathspec 1.1.1 (MPL-2.0, `dev`, ADR-004).
  Nunca cobre licença proibida nem Ultralytics.
- Decisão do humano (2026-10-28): exceção de licença para pathspec 1.1.1 (MPL-2.0, copyleft fraco em
  nível de arquivo), dependência transitiva de dev exigida pelo mypy (`Required-by: mypy`). Mesmas
  condições do certifi: só no grupo `dev`, sem modificação, sem redistribuição e fora da imagem da
  API. Presa a pacote, versão `==1.1.1` e grupo `dev`; outra versão ou o grupo `runtime` volta a
  falhar. Nenhuma outra licença foi aprovada.
- Registro de transitivas Python: `docs/licencas-transitivas.md`, gerado por `make licencas-sync`
  (união, nunca apaga; `--prune` explícito). Sem versão, de propósito. Rodar nos Python 3.11 e 3.12
  e commitar, pois marcadores de ambiente mudam a árvore. A verificação nunca escreve.
- Correções pós-revisão (escopo e limites):
  - Ids SPDX não comerciais (`CC-BY-NC-*`, `CC-BY-NC-SA-*`, `CC-BY-NC-ND-*` e qualquer id com `-NC`)
    são PROIBIDA, nunca DESCONHECIDA, e nenhuma exceção os cobre.
  - O registro das diretas é comparado também sob exceção: a linha de `docs/licencas.md` (ou de
    transitivas) deve ser igual à `licenca_verificada` da exceção. Para transitiva com licença
    ausente ou desconhecida coberta por exceção válida, `make licencas-sync` grava a
    `licenca_verificada`; sem exceção a linha fica `AUSENTE`/`DESCONHECIDA` e o gate falha. O modo de
    verificação continua sem escrever.
  - Imagens: a linha do registro casa de forma exata pelo repositório completo (namespace incluído;
    `evil/python` não herda a linha de `python`); sem casamento exato, a imagem é não registrada e
    o gate falha.
  - Escopo ampliado da descoberta de imagens (falha fechada): `Dockerfile`, `Dockerfile.*` e
    `*.Dockerfile` sob `docker/` (recursivo) e na raiz; `docker-compose*.yml|yaml` e
    `compose*.yml|yaml` na raiz e sob `docker/`.
  - Revisão final: chave de registro npm não dobra `-`/`_`/`.` (só Python usa PEP 503) e a tabela de
    diretas de `docs/licencas.md` vale para os dois ecossistemas; Ultralytics é detectada antes de
    qualquer `continue` em imagens e actions; "Non Commercial" e "Server Side Public License" em
    texto livre são PROIBIDA; entrada não-objeto no `package-lock.json` é erro de entrada (código 2);
    `--prune` sem `--write` é erro de uso; `LICENCAS_ARGS` só vale para `licencas-sync` (o alvo
    `licencas` nunca escreve).
  - Limite conhecido: lista de licenças em array no npm (`"license": [...]`) vira `OR` (basta uma
    permitida), coerente com o comportamento do npm legado; não há como saber offline se o autor
    quis `AND`.
- Limites: metadado que mente não é detectável; nenhuma licença é inferida por nome, página ou
  arquivo LICENSE; auditoria completa e revisão jurídica ficam para a F6-05.
- Consequência: `packaging` passa a ser importado (extra `dev`, registrado em `docs/licencas.md`);
  PyYAML e `packaging` são importados só dentro dos coletores.

