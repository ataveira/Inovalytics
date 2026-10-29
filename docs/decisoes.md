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
  tentativa. `BucketAlreadyOwnedByYou` conta como sucesso (corrida entre réplicas). Com
  `BucketAlreadyExists` o nome pode pertencer a outro dono: o startup repete `head_bucket` e só
  aceita se o bucket estiver acessível; 403/404 falha como erro permanente. O boto3 segue com
  `max_attempts=1`; o retry é laço próprio, sem biblioteca nova.
- Timeout: `asyncio.wait_for` não cancela a thread do boto3 (`to_thread`); ela termina sozinha,
  limitada por `connect_timeout`/`read_timeout`. É inofensiva, pois `head_bucket` e `create_bucket`
  são idempotentes.
- Variáveis `S3_STARTUP_*` vazias (ex.: linha `S3_STARTUP_TIMEOUT=` no `.env`) valem como não
  definidas e usam o padrão; `.env.example` as traz comentadas.
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
- Resolvido na F0-06: o filtro do smoke inclui também `pyproject.toml` e
  `backend/tests/test_compose_smoke.py`.
- Confirmado em 2026-10-28 no GitHub: Node 22 e as versões major das actions funcionam; o job `gate`
  passou em Python 3.11 e 3.12 (cerca de 40 s cada) e o `compose-smoke` manual passou (cerca de 2 min,
  incluindo o pull das imagens). Ainda falta ele ficar verde em 3 execuções seguidas para virar check
  obrigatório, e o erro de lint proposital (critério 17) não foi exercitado.

### Endurecimento (F0-06)

Pinagem: major
- Decisão D-CI1 (humano): manter actions por versão major (`checkout@v5`, `setup-python@v6`,
  `setup-node@v6`, `upload-artifact@v5`). `test_pinning_policy_matches_adr` exige que o workflow siga a
  linha "Pinagem:" acima; trocar para SHA exige mudar essa linha, os `uses:` e `licencas.md`.
- `actions/upload-artifact` (MIT, oficial) publica `smoke.xml` como `smoke-junit` (retenção 7 dias,
  `if: always()` filtrado pelo smoke, `if-no-files-found: ignore`). Sem permissão extra.
- Segredos no artefato: `smoke.xml` (e portanto o artefato `smoke-junit`) só contém credenciais
  descartáveis geradas ao acaso a cada execução do smoke, que vivem apenas no contêiner efêmero. Os
  testes de vazamento não imprimem o valor do segredo nem a saída da API na mensagem de falha
  (`pytest.fail` com texto fixo), para que nem o JUnit nem o log exponham o valor.
- Diagnóstico robusto: tolera `smoke.xml` ausente ou truncado (`ParseError`), só roda em falha e sem
  segredos. Timeouts: `gate` 15 min e `compose-smoke` 25 min (teto testado).
- Pendências do humano (não implementadas; registrar evidência aqui):
  - H1. PR descartável com erro de lint: `gate` vermelho em 3.11 e 3.12 (link: pendente).
  - H2. Smoke: execuções verdes consecutivas: 0/3 contadas (1 manual verde); depois, decidir se vira
    check obrigatório.
  - H3. Proteção de `main`: exigir `gate (python 3.11)` e `gate (python 3.12)`, PR obrigatório.
  - H4. Settings > Actions: permissões somente leitura, só actions do GitHub, aprovação para forks,
    retenção de artefatos/logs.
  - H5. Confirmar na execução real o upload de `smoke-junit` e que `upload-artifact@v5` resolve.
  - H6. Opcional: Dependabot para `github-actions` (necessário se um dia migrar para SHA).

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


## ADR-006: Modelo de dados e migrações (F1-04)

- Status: proposta (aguarda o humano nas pendências H1 a H7; os padrões abaixo foram assumidos
  para destravar a implementação e podem ser trocados).
- Decisão: esquema relacional `project`, `dataset`, `label_class`, `image`, `annotation` (caixa
  normalizada centro/largura/altura) em `backend/src/inovalytics/db/migrations/0001_initial.*.sql`;
  migrações em SQL puro executadas por executor próprio sobre asyncpg
  (`python -m inovalytics.db.migrate {up,down,status}`, `make migrate`). Sem ORM e sem dependência nova.
- Executor: tabela `schema_migrations(version, name, checksum_sha256, applied_at)` criada pelo
  executor; cada migração roda em transação junto com o seu registro; `pg_advisory_lock` com chave
  constante (`ADVISORY_LOCK_KEY`, ASCII "INOVMIGR") serializa execuções concorrentes; checksum
  SHA-256 do `up` barra edição de migração aplicada (migrações mescladas são imutáveis; mudanças
  entram em `0002_*`). Mensagens de erro nunca trazem DSN, senha nem texto de SQL.
- Execução: comando separado, nunca no startup da API (ADR-005: a API não depende do Postgres para
  subir). `api/main.py` não importa `inovalytics.db` (teste AST). Serviço `migrate` no Compose fica
  fora do escopo.
- Integridade no banco: FKs compostas `(id, project_id)` impedem anotação com classe de outro
  projeto, sem trigger. CASCADE de projeto/dataset/imagem para descendentes; `annotation -> label_class`
  é `NO ACTION` **e `DEFERRABLE INITIALLY DEFERRED`**. Achado em Postgres real: com `NO ACTION`
  imediato a exclusão em cascata de um projeto falha, porque a verificação da classe roda antes de a
  cascata dataset -> image -> annotation terminar. Adiada, a verificação ocorre ao fim do
  comando; excluir classe em uso continua falhando com violação de chave estrangeira (em transação
  explícita, o erro aparece no COMMIT).
- Caixa: CHECKs `0 <= x,y <= 1`, `0 < w,h <= 1` (rejeitam NaN e Infinity). Sem CHECK de contenção
  na imagem: caixa fora da imagem é relatório de qualidade da F1-10.
- Objetos órfãos: a exclusão em cascata remove só linhas; objetos no S3 ficam no bucket (limpeza
  posterior, F1-05/F4).
- Pendências do humano (padrão adotado entre parênteses):
  - H1. Ferramenta de migração (executor próprio; alternativa: Alembic + SQLAlchemy, MIT).
  - H2. Onde migrar (comando separado; alternativa: serviço `migrate` one-shot no Compose).
  - H3. `down` (só desenvolvimento e teste; em produção pode apagar dados). Recomendação futura: o executor não trava `down` em produção hoje; exigir flag explícita (ex.: `--i-know-this-drops-data`) antes de liberar o uso fora de desenvolvimento e teste.
  - H4. Caixa fora da imagem (aceita no banco, reportada na F1-10).
  - H5. Cascata sem apagar o S3 (aceita; limpeza assíncrona depois).
  - H6. Identificadores (`bigint` sequencial; alternativa: UUID quando houver autenticação, F4-01).
  - H7. Nomes de projeto e de classe (sensíveis a maiúsculas e minúsculas; alternativa: `lower(name)`).
- Testes: estáticos (regex) em `make check`; semântica em Postgres real em
  `backend/tests/test_db_postgres_docker.py` (marcador `docker`, `make compose-smoke`).

## ADR-007: Upload de imagens (F1-05)

- Status: proposta (aguarda o humano nas pendências H-F1-05-1 a H-F1-05-7; os padrões abaixo foram
  assumidos para destravar a implementação e podem ser trocados).
- Rota: `POST /datasets/{dataset_id}/images`, multipart, campo `files` (um ou mais) e campo opcional
  `split` (`train`, `val`, `test`). Resposta `{"images": [...]}` com `id`, `dataset_id`, `sha256`,
  `size_bytes`, `width_px`, `height_px`, `content_type`, `file_name`, `split`, `created`; 201 se
  alguma imagem foi criada, 200 se todas já existiam. Erros: `{"detail", "code", "file_index"}`.
- Multipart com `python-multipart` (Apache-2.0, versão em `docs/licencas.md`; não instalar o pacote
  homônimo `multipart`). Alternativa descartada: corpo bruto por `PUT` (sem lote, pior para a web).
- Limites em duas camadas: (1) middleware ASGI só nesta rota recusa com 413 por `Content-Length`
  acima de `UPLOAD_MAX_FILES * UPLOAD_MAX_BYTES + 64 KiB` e, sem `Content-Length` (chunked), conta os
  bytes e aborta a leitura; roda antes do parser, que grava o corpo em arquivo temporário;
  (2) o serviço lê cada arquivo em blocos de 64 KiB e responde 413 acima de `UPLOAD_MAX_BYTES`.
- Códigos: 404 dataset inexistente (antes de gravar no S3); 413; 415 conteúdo que não é JPEG nem PNG
  pela assinatura; 422 estrutura inválida, vazio, dimensão, arquivos demais, `split` ou `dataset_id`
  inválidos, sem arquivos, multipart malformado; 503 Postgres ou S3 indisponível. Nenhum corpo de
  erro traz DSN, endpoint, segredo ou caminho local.
- Imagem validada só pelo cabeçalho, com parser próprio em Python puro (sem Pillow): corpo corrompido
  com cabeçalho válido passa; detecção fica para a F1-10.
- Chave S3 `datasets/{dataset_id}/{sha256}.{ext}`, determinística por conteúdo; o nome do cliente só
  vai (sanitizado) para `image.file_name`. Tipo e extensão vêm da detecção, nunca do cliente.
- Ordem: valida todo o lote -> deduplica por `(dataset_id, sha256)` -> PUT no S3 -> `insert_image`;
  falha no insert após o PUT apaga o objeto se não houver linha (compensação). Janela residual
  (processo morto entre PUT e insert) deixa objeto órfão, aceito até a rotina de limpeza.
- Postgres na requisição sem ser obrigatório no startup (ADR-005): pool asyncpg preguiçoso criado no
  primeiro uso sob `asyncio.Lock`, falha não memorizada (vira 503), fechado no shutdown só se criado.
  `api/main.py` não importa `inovalytics.db`; só `api/images.py` importa (guardas da F1-04 relaxadas
  de forma mínima). O cliente S3 também é criado no primeiro uso.
- Recursos e concorrência: o parser multipart grava o corpo em arquivo temporário (spool) e a
  rota limita `max_files + 1` arquivos e 8 campos; o spool pode ocupar em disco até o limite total
  (`UPLOAD_MAX_FILES * UPLOAD_MAX_BYTES + 64 KiB`) antes de o 413 por arquivo sair. Não há limite
  de requisições simultâneas; aceito porque a API publica em `127.0.0.1` por padrão (`API_BIND`).
  A leitura do spool roda em thread (`asyncio.to_thread`), sem bloquear o event loop.
- Timeouts: o cliente S3 do upload usa connect 3 s e read `S3_UPLOAD_READ_TIMEOUT` (padrão 60 s);
  o de health segue com `HEALTH_CHECK_TIMEOUT`. Esperar conexão do pool além de 10 s dá 503
  `banco_timeout_conexao`. Checksums do boto3 ficam em `when_required` (compatibilidade com
  SeaweedFS), quando o botocore instalado aceita a opção.
- Erros de banco: violação de restrição (`IntegrityConstraintViolationError`) vira 409
  `conflito_integridade`; indisponibilidade/conexão vira 503 `banco_indisponivel`; qualquer outra
  falha vira 500 `erro_interno`, todas no formato `{detail, code, file_index}` e com log só do tipo.
- Janela de compensação: se duas requisições enviam os mesmos bytes ao mesmo tempo, a compensação
  (apagar o objeto após falha no insert) só apaga se não houver linha para o `sha256`; uma
  requisição que insere logo depois da checagem de outra pode deixar a linha apontando para um
  objeto já apagado (chave determinística por conteúdo). A janela é estreita e aceita até a
  rotina de limpeza/reconciliação (H-F1-05-7).
- Sem autenticação até a F4-01: não abrir `API_BIND` para a rede com este endpoint antes disso.
- Pendências do humano (padrão adotado entre parênteses):
  - H-F1-05-1. Limites padrão (20 MiB por arquivo, 20 arquivos, 16384 px por lado, 100 MP).
  - H-F1-05-2. Validar só o cabeçalho (alternativa: decodificar com Pillow, HPND, em tarefa à parte).
  - H-F1-05-3. Formatos aceitos (só JPEG e PNG).
  - H-F1-05-4. Deduplicação por dataset (alternativa: global no projeto, exige migração 0002).
  - H-F1-05-5. Endpoint aberto até a F4-01 (alternativa: chave de API provisória em `.env`).
  - H-F1-05-6. Lote validado como um todo (alternativa: aceitação parcial com relatório, 207).
  - H-F1-05-7. Limpeza de objetos órfãos (adiada; confirmar se vira tarefa antes da beta).
- Testes: HTTP com fakes em `backend/tests/test_api_images.py` (`make check`); fluxo real com
  Postgres e SeaweedFS em `backend/tests/test_upload_docker.py` (marcador `docker`, `make compose-smoke`).

## ADR-008: Importação COCO (F1-06)

- Status: proposta (aguarda o humano nas pendências H-F1-06-1 a H-F1-06-8; os padrões abaixo foram
  assumidos para destravar a implementação e podem ser trocados).
- Rota: `POST /datasets/{dataset_id}/imports/coco`, corpo `application/json` com o próprio arquivo
  COCO ("instances"), opções por query `dry_run` (padrão `false`) e `create_classes` (padrão `true`),
  ambas estritas (`true`/`false`/`1`/`0`; outro valor ou repetição dá 422 `query_invalida`).
  As imagens vêm da F1-05 (upload prévio); não há zip nem multipart, o que evita zip bomb, entradas
  com `../`, links simbólicos e arquivo temporário. Zip fica como tarefa futura (F1-06d).
- Parser próprio, só biblioteca padrão (`imports/coco_parser.py`), sem `pycocotools`: `json.loads`
  com rejeição de `NaN`/`Infinity` e de chave duplicada, checagem de profundidade sem pré-scan (ver
  débitos), limites
  de bytes, imagens, anotações, categorias e erros (`IMPORT_*`). Não é streaming: o limite de bytes
  protege a memória. O parse roda em thread (`asyncio.to_thread`) para não travar o event loop.
- Mapeamento de classes: por nome exato (após `strip`) com `label_class.name` do projeto; ausentes
  são criadas (todas as declaradas, em ordem de `id`, `position` a partir de `max+1`) com
  `create_classes=true`, ou viram erro `classe_ausente`. Classes existentes nunca são alteradas.
- Casamento de imagens por `file_name` reduzido ao último componente e sanitizado como no upload
  (comparação exata); nenhum caminho do arquivo toca disco ou S3. Nome repetido no dataset gera
  `imagem_ambigua`. Dimensões do COCO só são conferidas; a conversão usa as gravadas.
- Duas fases, tudo ou nada: fase 1 só lê e acumula todos os erros por item; fase 2 roda numa única
  transação com `pg_advisory_xact_lock` por projeto (`lock_timeout` de 10 s) e desfaz tudo, inclusive
  as classes criadas, em qualquer falha. `dry_run` executa só a fase 1 e leituras.
- Idempotência sem migração: insere só o excedente do multiconjunto `(imagem, classe, x, y, w, h)`
  em relação às anotações existentes; nunca apaga. Reimportar o mesmo arquivo cria 0 linhas.
- Caixa que ultrapassa a borda mas é representável é gravada sem recorte e relatada como aviso
  `caixa_fora_da_imagem`; a não representável (centro fora de `[0, 1]`, maior que a imagem) recusa a
  importação inteira (`caixa_nao_representavel`). `iscrowd=1` é ignorado e contado; segmentação,
  keypoints e URLs são ignorados (URLs jamais são acessadas).
- Corpo e limites: o `RequestBodyLimitMiddleware` da F1-05 ganhou o parâmetro `path` (limite por
  rota; uma instância por rota, comportamento do upload inalterado). A rota usa `IMPORT_MAX_JSON_BYTES`
  (16 MiB): 413 por `Content-Length` sem ler o corpo e por contagem em fluxo (chunked ou
  `Content-Length` mentiroso), abortando antes de acumular o corpo inteiro.
- Códigos: 201 se algo foi criado (anotações ou classes); 200 sem alterações ou em `dry_run`; 404
  `dataset_nao_encontrado` (verificado antes de ler o corpo, em conexão solta em seguida); 413
  `requisicao_grande_demais`; 415 `tipo_nao_suportado` (qualquer `Content-Type` que não seja
  `application/json`, ou `charset` diferente de UTF-8); 422 `importacao_invalida` (JSON, estrutura,
  limites e erros por item, com `erros`, `erros_total`, `erros_truncados`), `dataset_id_invalido` e
  `query_invalida`; 409 `dataset_alterado` (imagens mudaram durante a importação) e 409 `conflito_integridade` (violação de restrição ou erro de domínio do
  repositório na fase 2, tudo desfeito); 503 `banco_indisponivel`, `banco_timeout_conexao` e
  `importacao_em_andamento` (`LockTimeoutError`: outra importação do projeto segura o bloqueio; o
  cliente pode repetir); 500 `erro_interno`. Corpos e logs só trazem o tipo da exceção: nunca DSN,
  endpoint, segredo, caminho, SQL nem o JSON enviado. Cada mensagem de item é cortada em 300
  caracteres e o serviço limita o dado do usuário a 200.
- Relatório de sucesso: `status` (`importado`, `sem_alteracoes`, `simulado`), contagens de imagens,
  classes e anotações, `segmentacoes_ignoradas` e `avisos` (com total e truncamento).
- Estrutura: `inovalytics.imports` (independente de HTTP) e `api/imports.py` (segundo e último
  importador de `inovalytics.db` na camada `api`; `main.py` só registra a rota e continua sem importar
  `inovalytics.db`). A rota não usa S3. Guardas da F1-04 e da F1-05 ajustados de forma mínima.
- Sem dependência nova; licença dos dados importados (COCO oficial: CC BY 4.0, imagens com licenças
  variadas) não é verificada pelo importador, que ignora `info` e `licenses`; dados de clientes podem
  ter restrições próprias (registrar na documentação de usuário, F6).
- Sem autenticação até a F4-01: não abrir `API_BIND` para a rede com este endpoint antes disso.
- Pendências do humano (padrão adotado entre parênteses):
  - H-F1-06-1. Entrada: JSON bruto e imagens pela F1-05 (alternativas: zip com imagens e JSON em
    F1-06d, com limites de zip bomb; ou multipart).
  - H-F1-06-2. Casamento por nome base (alternativa: migração 0002 guardando o caminho original).
  - H-F1-06-3. Limites padrão (16 MiB, 50 000 imagens, 300 000 anotações, 1 000 categorias,
    profundidade 12, 100 erros no relatório).
  - H-F1-06-4. Idempotência por multiconjunto sem apagar (alternativas: substituir as anotações das
    imagens do arquivo; coluna `source` em migração 0002).
  - H-F1-06-5. Caixa que o esquema não comporta: recusar tudo (padrão) ou recortar com aviso.
  - H-F1-06-6. Criar classes ausentes automaticamente, todas as declaradas (padrão), só as usadas,
    ou exigir classes prévias.
  - H-F1-06-7. `iscrowd=1`: ignorar e relatar (padrão) ou tratar como erro.
  - H-F1-06-8. Endpoint aberto até a F4-01, como a F1-05 (H-F1-05-5).
- Débitos e ressalvas conhecidos (revisão F1-06):
  - CPU e memória do parse (corrige a versão anterior deste ADR). O pré-scan de profundidade por
    regex possessiva sobre bytes foi medido pelo revisor em 8 a 17 s de CPU por requisição com 32 MiB
    de muitos tokens (`[]` repetido, aspas soltas); como a regex roda em C e segura o GIL, congelava o
    servidor inteiro, e a afirmação anterior ("algumas centenas de ms", teste < 1 s) estava errada
    porque os testes usavam entradas pequenas. O pré-scan foi removido. Agora `json.loads` roda
    direto: o scanner C do CPython levanta `RecursionError` em cerca de 1000 níveis de forma barata
    (mapeado para `limite_excedido: max_depth`), e a profundidade <= `IMPORT_MAX_DEPTH` é conferida
    no objeto já parseado por percurso iterativo (pilha explícita, sem recursão; raiz = nível 1).
    Os erros e mensagens não mudaram.
  - Custos reais medidos (CPython 3.13, uma thread, `parse_coco` completo). Entradas hostis sem
    estrutura (`[` x N, `{"a":` x N, aspas soltas, `\"` repetido, string gigante aberta ou fechada):
    <= 0,06 s a 32 MiB. O custo real está no parse legítimo de muitos tokens pequenos, que
    materializa objetos Python: 32 MiB de `[],` ou `{},` levam 1,3 a 1,6 s e ~1,2 GiB de RSS; de `1,`
    2,3 s (o gancho `parse_int` é uma chamada Python por inteiro) e de `1.5,` 1,4 s. Com 16 MiB:
    0,6 a 1,0 s e ~700 MiB. Por isso o padrão de `IMPORT_MAX_JSON_BYTES` caiu de 32 para 16 MiB
    (a 32 MiB o pior caso passava de 2 s). Memória do `json.loads`: até ~45 vezes (732 MiB medidos para 16 MiB) o tamanho do
    arquivo no pior caso (listas ou objetos vazios), acima das "centenas de MB" estimadas antes; é o
    teto a considerar ao dimensionar o contêiner e ao subir o limite.
  - O parse em `asyncio.to_thread` evita bloquear o event loop só enquanto o interpretador
    alterna entre threads; o scanner C do `json.loads` (e a construção dos objetos) não libera o
    GIL, então uma requisição grande ainda atrasa as demais por até ~1 s a 16 MiB. Reduzir
    `IMPORT_MAX_JSON_BYTES` reduz esse atraso de forma linear; o remédio completo (processo separado
    ou parser em streaming) e o limite de concorrência por rota ficam pendentes, junto com a
    autenticação (F4-01). Testes de regressão (`test_coco_parser.py`) rodam o parser em thread com
    `join(timeout)` e exigem < 2 s por caso.
  - Dado o limite de 300 000 anotações, 16 MiB dão ~56 bytes por anotação: datasets reais maiores
    precisam subir o limite conscientemente (H-F1-06-3) ou ser divididos em vários arquivos.
  - TOCTOU de imagens: o bloqueio do projeto não impede upload/remoção de imagens (a F1-05 não o
    toma). Sob o bloqueio, a fase 2 relê as imagens do dataset e compara com o plano da fase 1; se
    divergirem, falha com HTTP 409 `dataset_alterado` (conflito de estado, não defeito do arquivo; o
    item em `erros` mantém o mesmo código), sem gravar nada (o cliente repete). Resta uma janela mínima entre a releitura e o commit, aceita: as anotações têm FK para
    a imagem, então uma remoção nessa janela vira `conflito_integridade` e desfaz tudo.
  - `lock_timeout` local: vale para a espera do advisory lock e também para espera de bloqueio de
    linha na mesma transação; ambos viram 503 `importacao_em_andamento`, mesmo quando a causa é uma
    escrita concorrente de linha e não outra importação. Mensagem imprecisa, comportamento seguro.
  - Nenhum 503 envia `Retry-After` (`banco_indisponivel`, `banco_timeout_conexao`,
    `importacao_em_andamento`): o cliente decide o intervalo. Débito para todos os 503 da API.
- Testes: HTTP com fakes em `backend/tests/test_api_coco_import.py` e `test_coco_core_static.py`
  (`make check`); fluxo real com Postgres em `backend/tests/test_coco_import_docker.py` (marcador
  `docker`, `make compose-smoke`; só o serviço `db`, imagens com `ObjectStore` em memória).
