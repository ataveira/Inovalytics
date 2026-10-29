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
- Checagem de DoS (regra acrescentada na F1-07): nenhum custo por item fora do coletor quando ele
  está cheio. Nada proporcional ao tamanho de um nome ou mensagem do usuário pode ser calculado por
  item que o `ErrorCollector` vai descartar (limite de 100 por tipo): mensagens com nome e o campo
  `file` são passados como funções sem argumentos (`LazyText`) e só são chamadas se o item for
  guardado (`coco_service._lazy`). `shown()` para ao juntar 200 caracteres limpos. Endurecimento
  de nomes (F1-07): `common.check_class_name` (usado só nas categorias) recusa controles, Cf (bidi,
  ZWSP, BOM) e nome vazio após ignorar invisíveis (espaços Unicode, Cf, preenchimentos como
  U+3164 e U+2800). U+200C (ZWNJ) e U+200D (ZWJ) são aceitos quando isolados entre dois
  caracteres visíveis (emoji compostos, persa, escritas indianas); no início, no fim, repetidos ou
  ao lado de espaço/invisível seguem recusados. `check_name` seguiu inalterado para `file_name`: testes da F1-06
  (`test_control_characters_and_bidi_are_stripped_from_error_messages` e
  `test_case_difference_does_not_match_and_error_is_safe`) fixam `file_name` com controles/bidi
  aceitos e saneados nas mensagens; mudar isso é decisão do humano (H-F1-07-9).
- Testes: HTTP com fakes em `backend/tests/test_api_coco_import.py` e `test_coco_core_static.py`
  (`make check`); fluxo real com Postgres em `backend/tests/test_coco_import_docker.py` (marcador
  `docker`, `make compose-smoke`; só o serviço `db`, imagens com `ObjectStore` em memória).

## ADR-009: Importação de rótulos no formato YOLO (txt) (F1-07)

- Status: proposta (aguarda o humano nas pendências H-F1-07-1 a H-F1-07-8; os padrões abaixo foram
  assumidos para destravar a implementação e podem ser trocados). Reaproveita o desenho do ADR-008
  (tudo ou nada, relatório por item, idempotência por multiconjunto, mesmos códigos HTTP).
- Rota: `POST /datasets/{dataset_id}/imports/yolo-txt`, opções por query `dry_run` e
  `create_classes`, iguais às da rota COCO. "YOLO" é aqui o nome de um formato de dados público
  (uma linha `classe cx cy w h` normalizada por imagem, mais a lista de nomes de classe); o
  Inovalytics não tem vínculo com a marca. A palavra aparece só no caminho da rota (constante única
  em `api/imports.py`), na documentação e, quando existir, no rótulo de interface "formato YOLO
  (txt)"; módulos, identificadores, códigos de erro e mensagens são neutros (teste estático em
  `test_txt_core_static.py`). Se o humano preferir zero ocorrência no caminho, a alternativa é
  `txt-normalizado` (troca da constante; H-F1-07-1).
- Entrada em JSON, não zip nem multipart. Corpo `application/json`:
  `{"names": [...] | {"0": "..."}` **ou** `"data_yaml": "<texto>"` (exatamente um; os dois juntos =
  `names_ambiguo`), `"labels": {"<arquivo>.txt": "<conteúdo>"}}`. Motivos, por superfície de DoS: o
  formato é um conjunto de arquivos pequenos e o JSON o representa sem arquivo temporário nem nomes
  de entrada interpretados pelo servidor; zip traz zip bomb, `../`, links simbólicos e arquivo
  temporário (tarefa própria, como a F1-06d); multipart traz limites de partes e cabeçalhos do
  parser de formulário e o limite de 20 arquivos da F1-05 não serve para milhares de rótulos; JSON
  reaproveita o que já foi medido e endurecido na F1-06 (`RequestBodyLimitMiddleware` por rota,
  `_read_body` em fluxo, rejeição de `NaN`/`Infinity`/chave duplicada, parse em
  `asyncio.to_thread`, 415 para outro `Content-Type`). Custo: o cliente monta o JSON. Chaves de
  `labels` são rótulos de texto, nunca caminhos usados em disco ou S3. A rota registra uma segunda
  instância do middleware de limite com o caminho próprio; cada rota tem o seu limite.
- `data.yaml` com parser mínimo próprio (`imports/names_yaml.py`), sem PyYAML. Motivos: PyYAML é só
  dependência de dev (usá-lo na API o promoveria a runtime); `safe_load` evita execução de código,
  mas não expansão de aliases ("billion laughs"), `RecursionError` em aninhamento profundo nem
  custo em linhas enormes; e YAML 1.1 converte escalares sem aspas (`no`, `off`, `1:30`) em outro
  tipo, o que estragaria nomes de classe. Subconjunto aceito: só `names` (quatro formas: lista e mapa
  de fluxo numa linha, lista e mapa de bloco) e `nc`; as demais chaves e seus filhos são ignorados
  sem interpretação; escalares simples, entre aspas simples ou duplas (só `\"` e `\\`); ids do mapa
  exatamente `0..n-1`; `nc` igual a `len(names)`. Tudo o mais (segundo documento, tabulação,
  `&`, `*`, `!`, `|`, `>`, `<<`, aninhamento em `names`, escalar vazio ou `null`) é
  `yaml_nao_suportado` com o número da linha. Sem expressão regular, uma passada linear, sem
  recursão; teste AST proíbe `re`, `yaml` e `ruamel` nos módulos novos. O cliente que tiver um
  `data.yaml` fora do subconjunto envia `names` em JSON.
- Linhas de rótulo: separadas só por `\n` (sem `splitlines`, para a numeração ser estável), BOM
  tolerado, linha vazia ignorada mas contada. Exatamente 5 campos; classe só com dígitos ASCII e menor
  que `len(names)`; números como token ASCII restrito a `0123456789+-.eE` antes do `float`, depois
  `math.isfinite`. Tolerância de 1e-6 nos intervalos, com ajuste (clamp) a `[0, 1]`; `w` ou `h`
  nulos ou negativos são `caixa_area_zero`. Caixa que ultrapassa a borda é gravada sem recorte e
  gera o aviso `caixa_fora_da_imagem` (mesma política da F1-06). Linha com 6 colunas
  (`coluna_extra`, provável confiança de predição) ou polígono de segmentação
  (`segmentacao_nao_suportada`) é erro por linha, não é ignorada em silêncio. Linhas repetidas são
  mantidas (multiconjunto). Cada erro cita `arquivo` e `linha`.
- Casamento rótulo-imagem por nome-base: a chave reduz-se ao último componente (`/` e `\`), é
  sanitizada como no upload e perde o `.txt`; a imagem perde só a última extensão; comparação exata,
  com distinção de maiúsculas. Sem imagem = `imagem_nao_encontrada`; duas imagens com o mesmo
  nome-base = `imagem_ambigua`; duas chaves com o mesmo nome-base = `rotulo_nome_duplicado`. Colide
  por desenho (`a.jpg` com `a.png`, `train/` com `val/`): recusa em vez de adivinhar (H-F1-07-2).
  Arquivo vazio é válido (imagem de fundo) e conta em `imagens_sem_caixas`; imagem do dataset sem
  arquivo de rótulo fica intacta. O `split` continua sendo o da F1-05.
- Reaproveitamento da F1-06 por código, não por cópia: `ItemError` ganhou `file` e `line`;
  `ImportRejectedError` é a base de `CocoImportError` (a rota captura a base); helpers de
  `coco_parser` foram para `imports/common.py` e o núcleo independente de formato (classes,
  excedente do multiconjunto, escrita em blocos na transação, relatório) para
  `imports/import_core.py`; em `api/imports.py`, `_with_repo` virou função de módulo e o preâmbulo
  da rota (id, query, tipo, `Content-Length`, dataset, leitura em fluxo) é compartilhado. Nenhum
  teste da F1-06 foi alterado. O parser roda com `collect_errors=True`: defeitos de linha e de
  casamento aparecem juntos no mesmo 422.
- Resposta: igual à do COCO, sem `ignoradas_crowd` nem `segmentacoes_ignoradas`, com
  `imagens_sem_caixas`. Erros e avisos ganham `arquivo` e `linha`, omitidos quando nulos, de modo que
  a resposta COCO não mudou. Mensagens não repetem linhas inteiras (só o token ofensivo truncado, ou
  nada). A rota não usa S3.
- Configuração: nova `IMPORT_MAX_YAML_BYTES` (padrão 65 536, regra `_positive`), repassada no
  `docker-compose.yml` e listada comentada em `.env.example`. As demais `IMPORT_*` valem também aqui;
  constantes não configuráveis: linha de 1024 caracteres, chave de 1024 caracteres. A contagem de
  linhas (`count("\n")`) é conferida antes de qualquer laço, o que impede que bytes de quebra de
  linha virem milhões de iterações em Python com o GIL preso.
- Guardas: `test_no_new_routes` liberou só a rota nova (commit separado); continuam valendo "só
  `api/images.py` e `api/imports.py` importam `inovalytics.db`" e "a rota de importação não toca S3".
- Sem dependência nova; `docs/licencas.md` inalterado. Nada da Ultralytics: o parser foi escrito a
  partir da descrição pública do formato, com fixtures sintéticas em código. A licença dos dados
  importados não é verificada (ressalva do ADR-008).
- Sem autenticação até a F4-01: não abrir `API_BIND` para a rede com este endpoint antes disso.
- Custos medidos (pelo implementador nas subtarefas 07b e 07c, CPython 3.13, uma thread, parser de
  rótulos e `data_yaml`; máquina: ambiente de desenvolvimento x86_64, 2 vCPU, CPython 3.13 (sandbox
  de CI do agente); repetir ao mudar limites ou hardware). Os testes de regressão rodam em thread com
  `join(timeout)` e limite >= 2x o valor medido, nunca abaixo do orçamento de 2 s por caso:

  | Caso | Tempo | Memória |
  | --- | --- | --- |
  | `data_yaml` de 64 KiB, pior caso entre as entradas hostis | 11,7 ms | não medida |
  | 300 000 caixas válidas (máximo legítimo) | 0,82 s | 87 MB de pico |
  | 1000 arquivos x 1000 erros cada (coletor limitado) | 0,46 s | 5 MB |
  | Casos de 16 MiB hostis (`\n`, uma linha, `0 0 0 0 0\n`, espaços) recusados | < 0,12 s | não medida |
  | 50 000 arquivos de rótulo de uma linha | 0,58 s | não medida |
  | 300 000 avisos `caixa_fora_da_imagem`, chave de 1024 caracteres (F1-07 correção) | 0,9 a 1,5 s (era 47,95 s: `shown(chave)` por aviso) | não medida |
  | 300 000 erros por linha (`0 x`), chave de 1024 caracteres | 0,13 a 0,22 s | não medida |

  Os casos de 16 MiB são recusados pelos limites antes dos laços; o custo real está no parse
  legítimo de muitas caixas. Como no ADR-008, o `json.loads` e o `str.split` seguram o GIL: uma
  requisição grande ainda atrasa as demais pelo tempo da tabela; remédio completo e limite de
  concorrência ficam com a F4-01. Os dois casos novos rodam em
  `test_txt_hardening.py` (thread com `join(timeout)`, limite de 3 s) e conferem o mesmo relatório
  de antes: total real de 300 000 e 100 itens guardados.
- Checagem de DoS: nenhum custo por item fora do coletor quando ele está cheio (regra do ADR-008,
  vale aqui). O parser calcula `shown(chave)` uma vez por arquivo; `ErrorCollector.add` aceita
  `message` e `file` como funções e só as chama se guardar o item; o serviço usa o mesmo mecanismo.
  O caso que reprovou a revisão: corpo de 6 MB, chave de 1024 caracteres, 300 000 linhas
  `0 0.99 0.5 0.5 0.5`, 48 s com o GIL preso.
- Separadores de coluna: só espaço e tab (vazios descartados; um `\r` final é tolerado). NBSP,
  U+2028, U+0085, U+001C a U+001F e demais espaços Unicode não separam: colam ao campo e viram
  `numero_invalido`, `classe_invalida` ou contagem de colunas errada (uma linha só com eles é uma
  coluna inválida, não linha em branco). Antes, `str.split()` aceitava todos como separador. Dois
  testes que fixavam o comportamento permissivo foram atualizados com justificativa no próprio
  teste (`test_lone_cr_is_not_a_line_break`, `test_line_numbers_use_only_lf`).
- `nc` entre aspas (`nc: "2"`) é recusado com `nc_invalido` e a linha; só inteiro simples vale.
- Nomes de classe (`names` e `data_yaml`): recusados controles, substitutos, Zl/Zp e Cf (bidi,
  ZWSP, BOM; ZWJ/ZWNJ só isolados entre caracteres visíveis) e nomes vazios após ignorar invisíveis
  (`nome_invalido`); a mesma regra vale
  nas categorias COCO (`common.check_class_name`, ver ADR-008).
- Pendências do humano (padrão adotado entre parênteses; nenhuma é D1 a D5):
  - H-F1-07-1. Nome da rota e uso da palavra "YOLO": `yolo-txt` e "formato YOLO (txt)" (padrão), ou
    `txt-normalizado`; confirmar também se a allowlist da F1-11 aceita o literal.
  - H-F1-07-2. Casamento por nome-base sem extensão e sem pasta, colisão recusa (padrão); alternativas:
    exigir a extensão da imagem em `labels` (`a.jpg.txt`) ou a migração 0002 da H-F1-06-2.
  - H-F1-07-3. `data.yaml`: parser mínimo próprio (padrão) ou PyYAML `safe_load` com limites,
    promovendo PyYAML a dependência de runtime (MIT, registro em `licencas.md` e
    `licencas-transitivas.md`).
  - H-F1-07-4. Polígono ou coluna extra: erro por linha (padrão) ou ignorar e contar como na F1-06.
  - H-F1-07-5. Atribuir `split` pela pasta da chave: fora desta tarefa (padrão); confirmar se vira
    tarefa própria.
  - H-F1-07-6. Tolerância de 1e-6 com ajuste (padrão) ou recusar qualquer valor fora de `[0, 1]`.
  - H-F1-07-7. Limites herdados da F1-06 e `IMPORT_MAX_YAML_BYTES` de 64 KiB; ajustar ao tamanho
    real dos datasets dos clientes.
  - H-F1-07-8. Endpoint aberto até a F4-01 (H-F1-05-5, H-F1-06-8); H-F1-06-4 a H-F1-06-7 valem aqui
    com o mesmo padrão e a mesma resposta do humano.
  - H-F1-07-9. Recusar também controles/Cf em `file_name` do COCO (hoje aceitos e saneados nas
    mensagens; exigiria mudar testes da F1-06).
- Testes: HTTP com fakes em `backend/tests/test_api_txt_import.py`; configuração e guardas em
  `test_txt_core_static.py`; serviço e parser em `test_txt_labels_service.py`,
  `test_txt_labels_parser.py` e `test_names_yaml.py`; fluxo real com Postgres em
  `backend/tests/test_txt_import_docker.py` (marcador `docker`, `make compose-smoke`).


## ADR-010: Importação Pascal VOC (F1-08)

- Status: proposta (aguarda o humano nas pendências H-F1-08-1 a H-F1-08-10; os padrões abaixo foram
  assumidos para destravar a implementação e podem ser trocados). Reaproveita o desenho dos ADR-008
  e ADR-009: tudo ou nada, relatório por item, idempotência por multiconjunto, mesmos códigos HTTP,
  mesma função de erros de banco (`_with_repo`), mesmo `RequestBodyLimitMiddleware` com instância
  e caminho próprios por rota.
- Rota: `POST /datasets/{dataset_id}/imports/pascal-voc`, query `dry_run` e `create_classes` (iguais
  às da F1-06/07). "Pascal VOC" é o nome público de um formato; sem vínculo com a marca, sem a
  palavra "yolo" nem nome de terceiros em módulo, identificador, código ou mensagem (teste estático
  em `test_voc_config_static.py`).
- Entrada: corpo `application/json` `{"annotations": {"<nome>.xml": "<texto XML>"}}` (obrigatório,
  pode ser `{}`; chaves extras ignoradas). JSON em vez de zip (zip bomb, `../`, links, arquivo
  temporário) e de multipart (limites do parser de formulário); XML cru (`application/xml`,
  `text/xml`) é recusado com 415. As chaves são rótulos de texto, nunca caminhos de disco ou S3.
- Respostas: 201 `importado`; 200 `sem_alteracoes` ou `simulado` (`dry_run`); 404
  `dataset_nao_encontrado`; 413 `requisicao_grande_demais` (por `Content-Length` e em fluxo); 415
  `tipo_nao_suportado`; 422 `importacao_invalida` (com `erros[]`: `code`, `kind`, `id`, `index`,
  `message`, `arquivo`, `linha`), `dataset_id_invalido`, `query_invalida`; 409 `dataset_alterado` e
  `conflito_integridade`; 503 `banco_indisponivel`, `banco_timeout_conexao`,
  `importacao_em_andamento`; 500 `erro_interno`. O relatório é o da F1-07 mais
  `ignorados{difficult, truncated, occluded, pose, partes}`, `sem_size` e `ajustes_origem_zero`.
  O parse roda em `asyncio.to_thread` (teste prova que o laço continua respondendo). A rota não usa S3.

### Parser XML: `pyexpat` com handlers que recusam (H-F1-08-1)

- Comparação: `xml.etree`/`minidom`/`sax` montam a árvore inteira (memória de 10 a 30 vezes o
  arquivo), habilitam DTD e entidades por padrão e não dão ganchos para abortar cedo: descartados.
  `defusedxml` (PSF-2.0) resolve o mesmo problema sobre o mesmo expat, mas é dependência nova de
  runtime e o subconjunto VOC dispensa DTD inteiro: é a alternativa se o humano preferir não manter
  código de segurança próprio. Parser mínimo próprio (regex, varredura de `<`): rejeitado, pois
  reimplementa referências de caractere, codificação e comentários, com divergência entre o que
  nós e outros leitores entendem. Escolhido: `xml.parsers.expat` da biblioteca padrão, sem
  dependência nova (`docs/licencas.md`, `licencas-transitivas.md` e `pyproject.toml` inalterados).
- Recusados com erro por arquivo (H-F1-08-2): todo DOCTYPE (`doctype_proibido`, antes de qualquer
  declaração de entidade existir), instrução de processamento (`instrucao_proibida`), CDATA
  (`cdata_proibido`), entidade não predefinida (`entidade_proibida`), codificação declarada além de
  UTF-8/US-ASCII (`encoding_nao_suportado`). Sem DOCTYPE só restam as cinco entidades predefinidas
  e referências numéricas, o que elimina por construção XXE de arquivo e de rede, entidade
  paramétrica, DTD externo, billion laughs e quadratic blowup, sem depender da versão do expat.
  XInclude não é processado (`xi:include` é só um elemento desconhecido). VOC real não usa
  DOCTYPE, PI nem CDATA; alguns exportadores raros emitem; o cliente remove.
- Entrega em chamada única `Parse(dados, True)`: o limite por arquivo torna isso seguro e evita o
  reparse quadrático de tokens longos em entrega fragmentada (CVE-2023-52425). `bytes.count(b"<")`
  é conferido antes de parsear.
- Mensagens de erro do expat viram códigos e frases fixas (nunca repetem o conteúdo);
  `lineno` vira `linha`.

### Desvios do plano original

- O parser (`voc_parser.py`) é puro e não conhece as dimensões da imagem. A conversão
  (`convert_box`), o aviso `caixa_fora_da_imagem` e a conferência de `<size>`
  (`tamanho_divergente`) ficam no serviço (`voc_service.py`), que conhece as dimensões gravadas.
- `ItemError` ganhou o campo `group` (posição do arquivo) para ordenar erros de objeto por arquivo
  de origem.
- Códigos: `xml_malformado` (qualquer defeito de sintaxe do expat, com a linha) e `classe_invalida`
  (nome de classe com controles, Cf/bidi, vazio após ignorar invisíveis) são códigos desta tarefa;
  o plano só os citava de passagem.
- `max_total_markup` (orçamento de `<` entre todos os arquivos) é constante do `VocLimits`
  (600 000), não variável de ambiente: o plano só pedia `IMPORT_MAX_XML_BYTES`.
- A conversão de caixa e o casador de imagens foram extraídos para o núcleo compartilhado
  (`common.py`/`import_core.py`) em commit próprio, sem alterar teste da F1-06/07.

- O teste docker (`test_voc_import_docker.py`) roda o app FastAPI no processo do teste, falando com
  o Postgres do contêiner; o plano original previa o app dentro do contêiner `compose`. Cobre a
  mesma rota, o mesmo SQL e o mesmo banco, mas não a imagem da API nem a rede do compose (isso fica
  com `compose-smoke` da F1-03).
- O planejamento (`_plan`, até 300 000 caixas, cerca de 1 s no pior caso) roda em
  `asyncio.to_thread` dentro de `import_voc`, como o parse. Os serviços COCO (`_plan_boxes`) e
  YOLO/txt (`_plan`) ainda planejam no laço de eventos: débito registrado (a troca é de uma linha,
  mas foi deixada para não mexer em código e testes das F1-06/07 nesta tarefa).

### Regras de domínio

- Geometria (H-F1-08-4): índices de pixel 1-based e inclusivos do devkit. Com `W`, `H` as dimensões
  GRAVADAS (nunca as do XML): `x0 = max(xmin - 1, 0)`, `x1 = xmax`, `cx = (x0 + x1) / (2W)`,
  `w = (x1 - x0) / W` (idem em y). Exemplo (imagem 200x100): `51,26,150,75` dá
  `(0.5, 0.5, 0.5, 0.5)`. `xmin=0` é aceito, ajustado e contado em `ajustes_origem_zero`.
  `xmax < xmin`, valor negativo ou não numérico = erro; `xmax == xmin` é válido. Passar da borda
  (tolerância 1e-6) grava sem recorte e gera aviso; o que o esquema não guarda é
  `caixa_nao_representavel`. Excesso de largura ou altura sobre a imagem inteira de até 1e-6 (a
  mesma tolerância) é arredondamento: a dimensão é recortada para W/H em vez de recusada.
- `<size>` (H-F1-08-5): opcional; presente e diferente do gravado (inclusive `0`) =
  `tamanho_divergente`; ausente = usa o gravado e conta `sem_size`.
- `difficult`, `truncated`, `occluded`, `pose`, `<part>` (H-F1-08-3): o esquema não os guarda;
  importa-se o objeto e conta-se em `ignorados`. Nada é descartado em silêncio.
- Casamento XML-imagem (H-F1-08-6): chave termina em `.xml`; `<filename>` com extensão cujo nome
  difere da chave = `nome_divergente`; com extensão e coerente, casa por nome exato (sensível a
  maiúsculas); sem extensão ou ausente, casa por nome-base (colisão = `imagem_ambigua`); duas
  chaves para a mesma imagem = `rotulo_nome_duplicado`. Imagem sem XML fica intacta.
- Classes (H-F1-08-8, H-F1-08-10): as de `<object><name>`, nome exato após `strip` (comparação
  byte a byte em código de ponto, sem NFC/NFD: "é" composto e decomposto são duas classes); `create_classes=true`
  cria só as usadas, na ordem da primeira aparição; `false` com ausente = `classe_ausente`.
- Lotes: o corpo é limitado a 16 MiB e a marcação total a 600 000 `<` (cerca de 7 100 arquivos
  típicos por requisição). Como a importação é idempotente, o cliente envia em lotes, cada um tudo
  ou nada.

### Limites (H-F1-08-7)

`IMPORT_MAX_JSON_BYTES` (16 MiB), `IMPORT_MAX_IMAGES` (50 000 arquivos), `IMPORT_MAX_ANNOTATIONS`
(300 000 objetos), `IMPORT_MAX_CATEGORIES` (1 000), `IMPORT_MAX_ERRORS` (100), nova
`IMPORT_MAX_XML_BYTES` (256 KiB por XML, em bytes UTF-8). Constantes: chave 1024 caracteres,
profundidade 16, 20 000 elementos por arquivo, 32 atributos, texto de campo 1024 caracteres,
`max_total_markup` 600 000. Todos conferidos antes dos laços e de construir estruturas de domínio.

### Regra de DoS: sem custo por item fora do coletor

Vale a regra dos ADR-008 e ADR-009: depois que o `ErrorCollector` está cheio, nenhum item custa
mais que a contagem. Mensagens, nomes de arquivo e linhas são `LazyText`/funções só chamadas se o
item for guardado (`ErrorCollector.add` aceita `message` e `file` como funções). O total real
continua exato (`erros_total`). Os limites de bytes, de `<` e de objetos saem em varreduras em C
antes do laço de parse, e o leitor aborta dentro dos handlers sem percorrer o resto.

### Medições reais

Máquina: Intel Xeon 2,1 GHz, 2 CPUs, Linux 6.18; Python 3.13.18; `pyexpat.EXPAT_VERSION` =
`expat_2.9.0`. Tempo: mediana de 3 execuções (1 nas sequências longas), chamada direta, sem rede,
sem banco. Pico de memória: `tracemalloc` em execução separada (infla o tempo, por isso não é
a mesma execução). O tempo inclui a decodificação do JSON no caso de corpo.

| Caso | Resultado | Tempo (ms) | Pico (MiB) |
|---|---|---|---|
| billion laughs clássico | `doctype_proibido` | < 0,1 | < 0,1 |
| billion laughs com entidade paramétrica | `doctype_proibido` | < 0,1 | < 0,1 |
| quadratic blowup (50 KiB x 50 000) | `doctype_proibido` | 0,2 | 0,5 |
| 256 KiB de `<a>` aninhados | `limite_excedido` | 0,2 | 0,3 |
| 256 KiB de `<a/>` | `limite_excedido` | 0,1 | 0,3 |
| 256 KiB de espaços | aceito (documento vazio de dados) | 0,9 | 0,8 |
| 256 KiB de comentários | aceito | 0,9 | 0,5 |
| um texto único de 256 KiB | `limite_excedido` | 0,3 | 0,8 |
| atributos (256 KiB) | `limite_excedido` | 10,3 | 5,9 |
| atributo de 200 KiB | aceito | 0,3 | 0,9 |
| nome de elemento de 256 KiB | aceito | 3,0 | 1,5 |
| `<a/>` 256 KiB sem teto de marcação (só limite de elementos) | `limite_excedido` | 10,1 | 1,3 |
| 1 000 arquivos hostis em sequência (sem teto de marcação) | todos recusados | 2 925 no total (2,9 por arquivo) | 1,3 |
| 5 arquivos de lixo de 256 KiB | recusados | 290 no total | 4,7 |
| corpo de 25 MiB (100 x 256 KiB) | `limite_excedido: max_json_bytes` | < 0,1 | < 0,1 |
| 16 MiB compactos (1 591 arquivos, 159 100 objetos, 2,23 milhões de `<`) | `limite_excedido: max_total_markup` | 49 | 32,2 |
| os mesmos 16 MiB sem o orçamento de marcação (só para referência) | aceito | 3 315 | 51,9 |
| no orçamento cheio, objetos compactos (4,3 MiB, 427 arquivos, 42 700 objetos, 598 654 `<`) | aceito | 733 (máx. 931) | 14,9 |
| 600 001 `<` (um arquivo a mais) | `limite_excedido: max_total_markup` | 10 | 8,7 |
| 7 300 arquivos típicos (1 002 bytes, 3 objetos, 84 `<`; 8,2 MiB; 613 200 `<`) | `limite_excedido: max_total_markup` | 38 | 16,7 |
| 7 142 arquivos típicos (o máximo que cabe no orçamento; 8,0 MiB) | aceito | 1 040 (máx. 1 072) | 16,3 |
| nomes hostis: 70 arquivos x 200 objetos, nome de 999 `a` + U+202E, 14,8 MiB, nenhum aceito | 14 000 `classe_invalida` | 430 (máx. 474; antes da correção: 1 905, máx. 2 293) | n/m |
| nomes hostis ZWJ: `(a ZWJ)*499 + a + U+202E`, 59 arquivos, 7 316 objetos, 14,7 MiB | 7 316 `classe_invalida` | 350 (máx. 417; antes: 2 267, máx. 2 516) | n/m |
| mesma forma, nomes válidos de 999 `a` (900 distintos), 14,7 MiB | aceito | 320 (antes: 510) | n/m |
| mesma forma, nomes válidos `(a ZWJ)*499 + a` (900 distintos), 14,6 MiB | aceito | 580 (máx. 603; antes: 2 606, máx. 3 094) | n/m |

Conclusões: (1) nenhum caso passa de 2 s; o pior caso legítimo (orçamento cheio) leva cerca de
0,7 a 1,1 s, então `max_total_markup` = 600 000 foi mantido e a margem de teste é >= 2x (testes com
limite de 4 s em `test_voc_budget.py` e `test_voc_parser.py`). (2) O pior caso por arquivo
isolado (atributos, 10 ms) e as recusas por DOCTYPE (< 1 ms) são desprezíveis. (3) O corpo de
16 MiB de objetos compactos NÃO é aceito: o orçamento de marcação o recusa em 49 ms; sem ele
custaria 3,3 s com o GIL preso, que é o motivo do orçamento. (4) O plano citava 7 300 arquivos
típicos; com o arquivo sintético de 84 `<` cabem 7 142 (com 70 `<`, de dois objetos, cabem 8 571).
(5) Nomes de classe (revisão da F1-08): `check_class_name` fazia um laço Python com
`unicodedata.category` por caractere e por `<object>`; um corpo de 15 MB de nomes quase válidos
custava 2,3 s com o GIL preso. Agora tabelas de pontos de código (frozenset calculado no import
do módulo com o `unicodedata` do processo, cerca de 0,35 s uma vez) servem de pré-filtro em C:
`set(nome).isdisjoint(proibidos)` decide sozinho todo nome sem ZWJ/ZWNJ, e nomes com joiner
passam por um `split` em C que confere só as bordas de cada parte; `name_is_blank` usa
`issuperset`. Um `lru_cache` foi descartado: a análise de módulos puros (`test_names_yaml.py`) só
admite `math`, `unicodedata` e poucos mais, e as medições não o exigiram. A semântica é a mesma: `test_class_name_equivalence.py`
compara a função antiga (copiada no teste) com a nova em todos os 1 114 112 pontos de código,
isolados, em contexto ('a'+cp+'a') e ao lado de joiners, mais fuzz de ZWJ/ZWNJ; COCO, txt e
`names_yaml` usam a mesma função e seus testes não foram tocados. Como todos os casos ficaram
abaixo de 1 s, não foi preciso um orçamento total de caracteres de nomes. `test_voc_name_dos.py`
afirma os tempos (< 1 s recusados, < 1,5 s aceitos) em thread com `join`.
(6) Reproduzir: `python backend/tests/_voc_bench.py` (script versionado, fora do `make check`;
usa os construtores de `test_voc_name_dos.py`); os casos de XML são os de
`backend/tests/_xml_cases.py` e a fixture de `test_voc_budget.py`.

### Pendências do humano (padrão adotado entre parênteses; nenhuma é D1 a D5)

- H-F1-08-1. Parser: `pyexpat` com handlers que recusam DOCTYPE (padrão) ou `defusedxml`.
- H-F1-08-2. Recusar DOCTYPE, PI e CDATA (padrão) ou aceitar DOCTYPE somente vazio.
- H-F1-08-3. `difficult=1`, `truncated`, `occluded`, `pose`: importar e contar (padrão), descartar
  `difficult` ou migração 0002 com colunas.
- H-F1-08-4. Convenção 1-based inclusiva com `xmin=0` ajustado e contado (padrão) ou 0-based.
- H-F1-08-5. `<size>` divergente ou zero = erro (padrão) ou ignorar com aviso.
- H-F1-08-6. `<filename>` autoritativo com recusa de divergência (padrão) ou só pela chave.
- H-F1-08-7. Limites (256 KiB por XML, 16 MiB de corpo, 50 000 arquivos, 300 000 objetos, 600 000
  `<` no total): ajustar ao tamanho real dos datasets dos clientes.
- H-F1-08-8. Classes: criar só as usadas (padrão), exigir prévias ou aceitar lista `names`.
- H-F1-08-9. Endpoint aberto até a F4-01 (como H-F1-05-5, H-F1-06-8, H-F1-07-8); `API_BIND` fica em
  127.0.0.1. As H-F1-06-4 a H-F1-06-7 valem aqui com o mesmo padrão.
- H-F1-08-10. Nomes de classe são comparados exatamente, sem normalização Unicode (padrão: "é"
  composto e decomposto viram classes distintas); alternativa: normalizar para NFC ao importar
  (afeta a identidade das classes já gravadas e as F1-06/07).
- Testes: HTTP com fakes em `test_api_voc_import.py`; XXE de ponta a ponta (arquivo sentinela e
  servidor local que falha se contatado) em `test_api_voc_xxe.py`; configuração e guardas em
  `test_voc_config_static.py`; orçamento em `test_voc_budget.py`; leitor, parser e serviço em
  `test_xml_reader*.py`, `test_voc_parser.py`, `test_voc_service.py`; fluxo real com Postgres em
  `test_voc_import_docker.py` (marcador `docker`, `make compose-smoke`).
