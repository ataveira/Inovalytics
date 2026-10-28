# Backlog Inovalytics

Status: [ ] pendente · [~] em andamento · [x] concluída

Revisado contra `docs/spec.md` em 28/10/2026. A partir desta revisão, as fases F1 a F6 do backlog coincidem com as Fases 1 a 6 do plano (a tabela de alinhamento antiga do spec, que usava F0 a F4, fica superada; F0 continua sendo o pré-requisito técnico). As tarefas F0-01 a F0-03 permanecem como histórico, sem alteração.

**Convenções**
- ID estável por tarefa (`Fx-nn`); não renumerar. Tamanho: S (até 1 dia), M (2 a 4 dias), L (mais de 4 dias). Toda tarefa M ou L deve ser dividida pelo planejador em subtarefas de até ~1 dia antes de ir para o `implementador`; as subtarefas usam o sufixo `a`, `b`, ... (ex.: F2-02a).
- Cada tarefa abaixo traz: dependências, tamanho e um critério de aceite curto e verificável. O detalhamento completo (arquivos, critérios numerados, riscos de licenciamento) é escrito quando a tarefa vira a próxima executável, como foi feito em F0-01 a F0-03.
- Decisões humanas pendentes (iguais às do spec, não assumir nenhuma resposta): **D1** código aberto ou fechado e licença; **D2** treino do zero ou ajuste de pesos permissivos; **D3** tamanho e composição da equipe; **D4** data de início real (o plano assume 02/11/2026); **D5** modelo de negócio. Tarefas que dependem delas levam `[bloqueada: decisão Dx]` e não podem ser iniciadas até o humano registrar a decisão em `docs/decisoes.md`.
- Regras transversais valem para todas as tarefas: nada de Ultralytics (nem código, nem pesos, nem `import`, nem como dependência opcional); só licenças permissivas; "YOLO" e a marca Ultralytics não entram no nome do produto, dos modelos ou dos artefatos (citar YOLOX como componente de terceiros e "formato YOLO" como formato de dados é permitido, com atribuição); toda dependência, imagem, peso ou dataset novo entra em `docs/licencas.md` no mesmo PR.

**Próxima tarefa executável: F0-04.**

## Fase 0: Fundação técnica (pré-requisito da Fase 1 do plano)
- [x] F0-01 Estrutura do repositório (backend/, frontend/, docs/, docker/), `pyproject.toml`, ruff, mypy, pytest
  - **Dependências:** nenhuma. **Tamanho:** menos de 1 dia; não dividir.
  - **Objetivo:** criar o esqueleto do monorepo e o gate de qualidade local (`make test`, `make lint`, `make check`) citado no CLAUDE.md, sem lógica de negócio, Docker Compose (F0-02), CI (F0-03) nem verificação automática de licenças (F0-04).
  - **Arquivos prováveis:**
    - `pyproject.toml` (Python >=3.11; config de ruff, mypy strict e pytest; extras `dev`)
    - `Makefile` (alvos `test`, `lint`, `check`; `check` = lint + mypy + test; front incluído quando houver `package.json`)
    - `backend/src/inovalytics/__init__.py` (com `__version__`), `backend/tests/test_smoke.py`
    - `frontend/package.json`, `frontend/eslint.config.js`, `frontend/vitest.config.ts`, `frontend/src/smoke.test.ts` (somente esqueleto vitest + eslint; sem React ainda, ou React mínimo)
    - `docker/.gitkeep` (ou `docker/README.md`)
    - `docs/licencas.md` (apenas registro inicial das dependências de dev: pytest, ruff, mypy, vitest, eslint; a automação fica em F0-04), `docs/decisoes.md` (ADR curto: layout do monorepo e ferramentas)
    - `.gitignore`, `.env.example` (sem credenciais), `README.md` curto em português
  - **Critérios de aceite (verificáveis):**
    1. `make test` termina com código 0 e executa pelo menos 1 teste pytest (`backend/tests/test_smoke.py`: importa `inovalytics` e confere que `__version__` é uma string não vazia).
    2. `make lint` termina com código 0 (`ruff check` e `ruff format --check` no backend; eslint no frontend).
    3. `make check` termina com código 0 e executa, nesta ordem, ruff, mypy (strict em `backend/src`) e pytest, além de vitest/eslint do frontend; um erro de tipo ou de lint introduzido de propósito faz `make check` terminar com código diferente de 0.
    4. Existem os diretórios `backend/`, `frontend/`, `docs/` e `docker/`; um teste pytest (`test_repo_layout`) verifica a existência deles e dos arquivos `pyproject.toml`, `Makefile`, `docs/licencas.md` e `docs/decisoes.md`.
    5. `pyproject.toml` exige `requires-python >= "3.11"`, e um teste lê o arquivo (tomllib) e confere isso e a presença das seções `[tool.ruff]`, `[tool.mypy]` e `[tool.pytest.ini_options]`.
    6. Teste de licenciamento (`test_no_ultralytics`): varre `pyproject.toml`, `backend/`, `frontend/package.json` e `docker/` e falha se encontrar `ultralytics` como dependência ou `import ultralytics` (o próprio teste e `docs/` ficam fora da varredura).
    7. Toda dependência declarada em `pyproject.toml` e `frontend/package.json` aparece em `docs/licencas.md` com nome, versão, licença SPDX e URL da fonte; um teste confere essa correspondência.
    8. `.env` está no `.gitignore` e existe `.env.example`; um teste confere que nenhum arquivo versionado contém padrões de credencial (ex.: `password=` com valor real).
    9. Instalação limpa reproduzível: em venv novo, `pip install -e ".[dev]"` seguido de `make check` passa.
  - **Riscos de licenciamento:**
    - Regra nº 1: nenhuma dependência da Ultralytics (AGPL-3.0), direta ou transitiva; não incluir YOLO/ultralytics nem como extra opcional. Conferir com `pip show`/`pip-licenses` que a árvore resolvida não a traz.
    - Ferramentas de dev devem ser permissivas: pytest (MIT), ruff (MIT), mypy (MIT), vitest (MIT), eslint (MIT). Evitar dependências GPL/AGPL/SSPL em dev ou runtime; registrar licença de cada uma em `docs/licencas.md` (exigência do CLAUDE.md).
    - Não copiar templates, configs ou scaffolding de repositórios da Ultralytics ou de outras fontes sem licença clara; escrever a configuração do zero.
    - Declarar a licença do próprio projeto só depois de resolvida a decisão pendente em `docs/spec.md` (código aberto ou fechado); em `pyproject.toml` deixar o campo de licença fora ou marcado como pendente, sem presumir.
    - A spec (`docs/spec.md`) ainda é um placeholder; as decisões de layout acima devem ser registradas em ADR para revisão.
- [x] F0-02 `docker-compose.yml` com PostgreSQL, armazenamento S3 e API FastAPI (healthcheck)
  - **Dependências:** F0-01 (concluída). **Tamanho:** cerca de 1 dia; não dividir, mas implementar em 3 commits (API + testes; Dockerfile + compose; docs/ADR/licenças).
  - **Objetivo:** subir com `docker compose up` três serviços (PostgreSQL, armazenamento compatível com S3 e API FastAPI) com healthcheck em todos, credenciais só via `.env`, sem lógica de negócio (modelo de dados fica na F1-01, upload na F1-02).
  - **Decisão prévia (ADR-002, depende de aprovação humana):** ver "Recomendação sobre o MinIO" abaixo. O compose usa variáveis `S3_*` genéricas, para que o backend não dependa do servidor escolhido.
  - **Arquivos prováveis:**
    - `docker-compose.yml` (raiz), `docker/api/Dockerfile`, `.dockerignore`
    - `backend/src/inovalytics/api/__init__.py`, `backend/src/inovalytics/api/main.py` (app FastAPI, `GET /health` liveness e `GET /health/ready` que checa Postgres e S3), `backend/src/inovalytics/config.py` (settings lidos do ambiente)
    - `backend/tests/test_api_health.py`, `backend/tests/test_compose_static.py`, `backend/tests/test_dockerfile_static.py`
    - `pyproject.toml` (dependências de runtime), `.env.example` (novas variáveis, sem valores reais), `Makefile` (alvo opcional `make compose-smoke`, fora do `make check`)
    - `docs/licencas.md`, `docs/decisoes.md` (ADR-002), `docker/README.md`
  - **Critérios de aceite estáticos (rodam em `make check`, sem Docker):**
    1. `docker-compose.yml` é YAML válido (parse com PyYAML ou ruamel, só como dependência de dev registrada em `licencas.md`) e define exatamente os serviços `db`, `storage` e `api`.
    2. Cada serviço tem `healthcheck` (com `test`, `interval`, `timeout`, `retries`); `api` tem `depends_on` de `db` e `storage` com `condition: service_healthy`.
    3. Toda imagem tem tag fixa explícita: nenhuma sem tag e nenhuma `:latest`.
    4. Nenhum segredo literal no compose: senhas e chaves vêm de `${VAR}` (sem default com valor real, exceto a forma `${VAR:?mensagem}`); cada `${VAR}` usada existe em `.env.example`; `.env` continua no `.gitignore`.
    5. Postgres e armazenamento têm volume nomeado declarado; só a porta da API é publicada no host (Postgres e storage ficam na rede interna, ou ligados a `127.0.0.1`).
    6. Dockerfile da API: base `python:3.11+-slim` com tag fixa, `USER` não root, `HEALTHCHECK` ou healthcheck equivalente no compose, sem `COPY .env`, sem `pip install ultralytics`; `.dockerignore` exclui `.env`, `.git` e `.venv`.
    7. `test_no_ultralytics` (da F0-01) continua verde, agora cobrindo `docker-compose.yml` e `docker/`.
    8. Teste de licenças da F0-01 (critério 7) continua verde: FastAPI, uvicorn, driver de Postgres e demais dependências novas aparecem em `docs/licencas.md` com versão, SPDX e URL.
    9. Testes de API com `fastapi.testclient`/httpx: `GET /health` retorna 200 e `{"status": "ok"}` sem tocar em banco nem storage; `GET /health/ready` retorna 200 com dependências simuladas (fakes injetadas) saudáveis e 503 quando qualquer uma falha, sem vazar senha ou DSN no corpo. `make check` (ruff, mypy strict, pytest) passa.
  - **Critérios de aceite que exigem Docker (rodam em `make compose-smoke`, marcados `@pytest.mark.docker` e pulados com `skip` se `docker` não existir; obrigatórios na revisão humana ou no CI da F0-03):**
    10. `docker compose config -q` termina com código 0 usando `.env.example` preenchido com valores de teste.
    11. `docker compose build api` termina com código 0.
    12. `docker compose up -d --wait` termina com código 0 (todos os healthchecks `healthy` em até 120 s).
    13. `curl -f http://localhost:<porta>/health/ready` retorna 200; após `docker compose stop db`, o mesmo endpoint retorna 503 e o contêiner da API continua rodando.
    14. `docker compose down -v` remove tudo; um segundo `up` a partir do zero funciona (idempotência).
  - **Riscos de licenciamento:**
    - Nenhuma imagem ou dependência da Ultralytics. Registrar em `licencas.md` também as imagens (nome, tag, licença), não só os pacotes.
    - Dependências Python novas: FastAPI (MIT), Starlette (BSD-3-Clause), Pydantic (MIT), uvicorn (BSD-3-Clause), httpx para testes (BSD-3-Clause), PyYAML para testes (MIT), boto3 ou cliente S3 equivalente (Apache-2.0). Confirmar as versões e a árvore transitiva com `pip-licenses` antes de registrar.
    - Driver PostgreSQL: psycopg 3 é LGPL-3.0 (e `psycopg-binary` embute libpq, licença PostgreSQL). Uso por importação dinâmica sem modificar é compatível mesmo em produto fechado, mas é copyleft fraco e entra em conflito com a decisão pendente "código aberto ou fechado". Alternativa permissiva: asyncpg (Apache-2.0). Recomendação: usar asyncpg, ou registrar psycopg no ADR como exceção LGPL aprovada pelo humano. Não definir ORM aqui (F1-01).
    - Imagem `postgres` (licença PostgreSQL, permissiva; a base Debian inclui pacotes GPL, aceitável por ser distribuição de imagem oficial sem modificação). Fixar a versão major (ex.: 16 ou 17).
    - MinIO: ver abaixo.
    - Imagem base `python:3.x-slim` (PSF-2.0 + pacotes Debian). Não copiar Dockerfiles de terceiros; escrever do zero.
  - **Recomendação sobre o MinIO:**
    - O servidor MinIO é AGPL-3.0. Rodá-lo sem modificações como contêiner separado e falar com ele só pela rede (API S3) não obriga a abrir o código do Inovalytics, porque não há vínculo de linkagem nem trabalho derivado. Isso é juridicamente defensável, mas as obrigações recaem sobre quem distribui: o instalador on-premise (F4-02) que embalar a imagem MinIO passa a distribuir AGPL e deve oferecer o código-fonte e os avisos; qualquer patch no MinIO obriga a publicar o patch.
    - Ele também entra em choque com a Regra nº 1 do CLAUDE.md ("somente componentes permissivos"), que o próprio CLAUDE.md contradiz ao listar MinIO na stack. Há ainda risco de manutenção: o MinIO Community passou a ter distribuição de imagens/binários reduzida, e funcionalidades do console foram movidas para a edição comercial; confirmar o estado atual antes de fixar a tag.
    - Recomendação: tornar o armazenamento intercambiável por configuração (`S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, cliente boto3) e usar por padrão no compose o SeaweedFS (Apache-2.0, gateway S3), com tag fixa. Alternativa permissiva a avaliar: Zenko CloudServer (Apache-2.0). Evitar Garage (AGPL-3.0) e Ceph RGW (LGPL, pesado demais para on-premise simples).
    - Se o humano preferir manter o MinIO (por maturidade ou pelo CLAUDE.md), aceitar apenas como serviço externo separado, imagem oficial sem modificação, em perfil opcional do compose (`profiles: ["minio"]`), com ADR-002 registrando: sem patches, sem inclusão no instalador sem cumprir AGPL, e aviso em `licencas.md`.
    - Ação necessária do humano: aprovar o ADR-002 e, se a escolha for SeaweedFS, atualizar a linha "Stack padrão" do CLAUDE.md. O implementador não deve alterar o CLAUDE.md sozinho.
  - **Fora de escopo:** migrações, modelo de dados, upload de imagens, autenticação, TLS/proxy reverso, CI (F0-03), verificação automática de licenças (F0-04).
- [x] F0-03 CI no GitHub Actions rodando `make check` (critérios 15-17: verificação humana no GitHub pendente)
  - **Dependências:** F0-01 e F0-02 (concluídas). **Tamanho:** menos de 1 dia; não dividir, mas 2 commits (workflow + testes estáticos; docs/ADR/licenças).
  - **Objetivo:** todo push e pull request roda o mesmo gate local (`make check`) em ambiente limpo, com permissões mínimas, sem segredos e sem Ultralytics; o fluxo de Compose com Docker (`make compose-smoke`) roda em job separado que não bloqueia o gate rápido.
  - **Decisões de projeto:**
    - Gatilhos do `ci.yml`: `push` (branches `main` e `feat/**`) e `pull_request`; evitar duplicidade de execução push+PR usando `concurrency`. `workflow_dispatch` também habilitado (permite rodar o smoke manualmente).
    - Job `backend`: matriz Python 3.11 e 3.12 (3.11 é o mínimo do `requires-python`; 3.12 é a base do Dockerfile), `pip install -e ".[dev]"`, Node 20 ou 22 LTS, `npm ci` em `frontend/` (há `package-lock.json`), depois `make check`. Um único job cobre backend e frontend, porque `make check` já inclui eslint e vitest; evita separar e duplicar o gate. Se o tempo ficar alto, dividir depois.
    - Job `compose-smoke` (Docker, `make compose-smoke`): roda em `push` para `main`, em `workflow_dispatch` e em PR somente quando os arquivos `docker-compose.yml`, `docker/**`, `.env.example` ou `backend/src/**` mudarem (filtro por `paths` em job separado via `dorny/paths-filter` é terceiro e não oficial: evitar; alternativa sem dependência: `git diff --name-only` em um passo de shell). Recomendação mais simples e segura: `main` + manual + PR com label ou caminho filtrado por passo de shell. Motivo: puxa imagens grandes (postgres, SeaweedFS), é mais lento e depende de tags ainda não confirmadas (`postgres:17.6`, `chrislusf/seaweedfs:3.97`, `python:3.12.11-slim-bookworm`); a primeira execução no CI é justamente a confirmação registrada em `licencas.md`. Não faz parte do `needs` do job principal. Marcar como obrigatório na proteção de branch só depois de 3 execuções verdes seguidas.
    - Segurança: `permissions: contents: read` no nível do workflow; nenhum job pede mais. Sem `secrets.*` (o smoke gera credenciais aleatórias, como o próprio teste já faz). Sem `pull_request_target`. `actions/checkout` com `persist-credentials: false`. Concurrency: `group: ci-${{ github.workflow }}-${{ github.ref }}`, `cancel-in-progress: true` (no `main`, avaliar `false` para não cancelar o smoke de merge). `timeout-minutes` em todos os jobs (ex.: 15 para o gate, 25 para o smoke).
    - Actions usadas só as oficiais: `actions/checkout`, `actions/setup-python` (cache `pip`), `actions/setup-node` (cache `npm`, `cache-dependency-path: frontend/package-lock.json`). Fixar por SHA de commit completo com comentário da versão major/tag (preferido; fixar apenas por `@v4` é aceitável se o humano preferir menos manutenção, mas fica registrado no ADR). Habilitar Dependabot para `github-actions` é opcional e fica fora do escopo.
  - **Arquivos prováveis:**
    - `.github/workflows/ci.yml`
    - `backend/tests/test_ci_static.py` (usa PyYAML, já dependência de dev; roda em `make check`)
    - `Makefile` (somente se faltar algo, ex.: nada novo é esperado; não mudar a ordem de `check`), `docs/decisoes.md` (ADR-003), `docs/licencas.md` (seção "GitHub Actions"), `README.md` (selo/nota curta, opcional), `docker/README.md` (ponteiro para o smoke no CI)
  - **Critérios de aceite (verificáveis por teste estático em `make check`, sem rede e sem Docker):**
    1. `.github/workflows/ci.yml` existe e é YAML válido. Atenção: PyYAML (YAML 1.1) lê a chave `on` como `True`; o teste deve tratar `data.get("on", data.get(True))`.
    2. Gatilhos: contém `push` e `pull_request` (e `workflow_dispatch`); não contém `pull_request_target`.
    3. Jobs presentes: um job de gate (ex.: `check`) e um job `compose-smoke`; o gate chama exatamente `make check` em algum `run`; o smoke chama `make compose-smoke`; o gate instala com `pip install -e ".[dev]"` e `npm ci` (com `working-directory: frontend` ou `--prefix frontend`) antes de `make check`.
    4. Matriz do gate inclui `3.11` e `3.12` (strings, não floats: `3.10` viraria `3.1` em YAML; o teste confere o tipo `str`). Nenhuma versão abaixo de 3.11.
    5. `permissions` no nível do workflow é exatamente `{contents: read}`; nenhum job declara permissão de escrita (`write`) nem `id-token`; nenhum uso de `pull_request_target`.
    6. Todo job tem `timeout-minutes` inteiro e positivo (gate <= 30, smoke <= 45); `concurrency` existe com `cancel-in-progress: true`.
    7. Toda `uses:` pertence a `actions/` (checkout, setup-python, setup-node) e está fixada em SHA de 40 hex (ou, se o ADR-003 optar por tag, em `@vN` major, nunca `@main`, `@master` ou sem versão); `actions/checkout` tem `persist-credentials: false`. Uso de `uses:` de organizações não oficiais faz o teste falhar.
    8. Cache habilitado: `setup-python` com `cache: pip` e `setup-node` com `cache: npm` (e `cache-dependency-path` apontando para `frontend/package-lock.json`, que deve existir).
    9. Sem segredos: nenhuma ocorrência de `secrets.` (exceto, se algum dia necessário, `secrets.GITHUB_TOKEN`; hoje o teste exige zero), nenhum literal que case com os padrões do `test_no_secrets` (senha, chave, token) no workflow; `env` do workflow não define `POSTGRES_PASSWORD`, `S3_SECRET_KEY` etc. com valor literal; nenhum `curl | sh`/`wget | bash`.
    10. Sem Ultralytics: `ultralytics` ausente do YAML; `test_no_ultralytics` (F0-01/F0-02) continua verde e agora varre também `.github/` (conferir que `project_files` inclui o arquivo novo: usa `git ls-files --others`, então basta o arquivo não estar ignorado; o teste de CI afirma que `.github` não está no `.gitignore`).
    11. O smoke não fica no caminho crítico: `compose-smoke` não é dependência (`needs`) do gate, e o gate não depende dele; o smoke não tem `continue-on-error` oculto (se tiver, o teste exige comentário/ADR; preferir ausente).
    12. Consistência com o repositório: versões de Python do workflow >= `requires-python` do `pyproject.toml`; o Node do workflow está na faixa declarada em `frontend/package.json` (`engines`, se existir; se não existir, adicionar ou documentar no ADR).
    13. `docs/licencas.md` tem seção "GitHub Actions" com cada action usada (nome, versão/SHA, licença SPDX MIT, URL) e `test_licencas` confere a correspondência com o workflow (novo teste: toda `uses:` aparece no documento).
    14. `make check` completo continua verde localmente (ruff, mypy strict, pytest, eslint, vitest), incluindo o novo teste (tipado, passa em `ruff format --check`).
  - **Critérios que exigem o GitHub (verificação humana ou na primeira execução, não automatizáveis aqui):**
    15. Execução do workflow no PR da branch `feat/F0-03`: job do gate verde nas duas versões de Python; tempo total registrado no ADR.
    16. `compose-smoke` disparado manualmente (`workflow_dispatch`) fica verde, o que confirma as tags `postgres:17.6`, `chrislusf/seaweedfs:3.97` e `python:3.12.11-slim-bookworm`; se alguma tag não existir, corrigir `docker-compose.yml`/Dockerfile/`licencas.md` (fora do escopo do CI, abrir tarefa ou ajuste pequeno separado) e remover o aviso "tags a confirmar" de `licencas.md`.
    17. Um erro de lint introduzido de propósito num PR de teste faz o job falhar (prova de que o gate bloqueia).
  - **Riscos:**
    - **Licenciamento das actions:** `actions/checkout`, `setup-python` e `setup-node` são MIT, mantidas pela GitHub; registrar em `licencas.md` e checar a licença no repositório de cada uma na versão fixada. Executam só no runner do GitHub e não são distribuídas com o produto. Proibido usar actions de terceiros sem licença permissiva registrada, e qualquer action ou passo que instale Ultralytics. Os termos do GitHub Actions (runners hospedados) não são software do projeto, mas o CI depende de serviço proprietário; ADR-003 deve registrar que o `Makefile` continua sendo a fonte da verdade (portável para outro CI on-premise, p. ex. Forgejo/Gitea Actions).
    - **Imagens no smoke:** as mesmas do compose (postgres, SeaweedFS, python), já registradas; o runner `ubuntu-latest` traz Docker e Compose v2, sem instalar nada. Fixar a imagem do runner em `ubuntu-24.04` em vez de `ubuntu-latest` para reprodutibilidade.
    - **Token de push sem escopo `workflow`:** o push de qualquer arquivo em `.github/workflows/` é rejeitado se o token (PAT clássico sem `workflow`, ou GitHub App/fine-grained sem `Workflows: write`) não tiver essa permissão; a mensagem típica é "refusing to allow ... to create or update workflow ... without `workflow` scope". Alternativas, em ordem: (a) o humano renova o token com escopo `workflow` (PAT clássico) ou permissão "Workflows: read and write" (fine-grained); (b) o implementador deixa o arquivo pronto e commitado só localmente/em patch (`docs/ci/ci.yml.proposto` ou anexo) e o humano o copia para `.github/workflows/ci.yml` pela interface web do GitHub ou com credencial própria; os testes estáticos apontam para o caminho final, então nesse caso devem ficar marcados `skip` ou `xfail` com motivo até o arquivo existir (decisão do humano, para não deixar `make check` vermelho); (c) criar o arquivo pela UI web do GitHub. Não contornar com `GITHUB_TOKEN` embutido nem commitar credenciais. Se o push falhar, a tarefa fica `[~]` e o implementador registra o bloqueio, sem marcar concluída.
    - **Tags de imagem não confirmadas:** o smoke pode falhar na primeira execução por motivos alheios ao CI; por isso não bloqueia PR até estabilizar.
    - **Falsos positivos do teste YAML:** a chave `on` virando `True`, versões de Python como float e `ruff format` sobre o teste; cobertos nos critérios 1 e 4.
    - **Custo/tempo:** matriz dobra o tempo; o cache de pip/npm e `cancel-in-progress` mitigam. Execução de `make check` instala `node_modules` via `frontend-deps` apenas se faltar, então `npm ci` antes é necessário para ser determinístico.
    - **Segurança de PRs de forks:** sem segredos e com `contents: read`, PRs externos são seguros; não adicionar `pull_request_target` nem passos que executem código do PR com token escrevível.
  - **Fora de escopo:** CD/publicação de imagens, registry, release, Dependabot, verificação automática de licenças (F0-04), cobertura de testes, badges avançados, proteção de branch (configuração do repositório, ação do humano).
- [ ] F0-04 `docs/licencas.md` e verificação automática de licenças das dependências (PRÓXIMA TAREFA EXECUTÁVEL)
  - **Dependências:** F0-01, F0-02, F0-03 (concluídas). **Tamanho:** S (menos de 1 dia; não dividir).
  - **Objetivo:** transformar o registro manual de `docs/licencas.md` em verificação automática dentro de `make check`, bloqueando dependência sem registro ou com licença proibida.
  - **Arquivos prováveis:** `backend/src/inovalytics/tools/license_check.py` (ou `scripts/check_licenses.py`), `backend/tests/test_license_check.py`, `Makefile` (passo no `check`), `docs/licencas.md` (seções com formato estável, parseável), `docs/decisoes.md` (ADR-004: política de licenças e lista de exceções).
  - **Critérios de aceite:**
    1. Todo pacote declarado em `pyproject.toml` e `frontend/package.json` e toda imagem do `docker-compose.yml` aparece em `docs/licencas.md` com versão, SPDX e URL; a falta faz o `make check` falhar.
    2. A licença de cada dependência resolvida, inclusive transitiva, é lida sem rede e está na lista permissiva (MIT, BSD-2/3-Clause, Apache-2.0, ISC, PSF-2.0, PostgreSQL, 0BSD e equivalentes); GPL, AGPL, LGPL, SSPL e "sem licença" falham, exceto exceções listadas com ID de ADR (hoje, só certifi/MPL-2.0, ADR-002). Fontes: pacotes Python vêm dos metadados instalados (`importlib.metadata`); pacotes npm vêm da leitura de `frontend/package-lock.json` (árvore resolvida, com o campo `license` de cada entrada em `packages`; sem rodar `npm` nem acessar a rede). Escopo: o extra `dev` e o runtime, ambos, e a saída da verificação separa os dois grupos (`runtime` e `dev`) para cada ecossistema; entrada sem classificação de grupo falha fechado.
    3. Teste negativo com metadados simulados (fixture): um pacote AGPL falso e um pacote ausente do registro fazem a verificação falhar com mensagem que cita o pacote.
    4. A verificação falha se `ultralytics` aparecer na árvore resolvida, nas actions ou nas imagens (complementa `test_no_ultralytics`).
    5. `make check` verde em Python 3.11 e 3.12 no CI, sem acesso à rede dentro do teste.
  - **Riscos de licenciamento:** preferir só biblioteca padrão (`importlib.metadata`, `tomllib`, `json`); se entrar ferramenta como `pip-licenses` (MIT), registrar. Campos de licença de pacotes são inconsistentes (texto livre vs SPDX): tratar com tabela de normalização e falhar fechado (desconhecido = bloqueia). Não é parecer jurídico.
  - **Fora de escopo:** pesos pré-treinados (F1-01), SBOM de release (F6-05).
- [ ] F0-05 Mover a criação do bucket S3 do `/health/ready` para o startup da API (dívida do ADR-002)
  - **Dependências:** F0-02. **Tamanho:** S. **Aceite:** `GET /health/ready` não escreve no storage (teste com fake que falha em qualquer chamada de escrita); o bucket é criado no startup de forma idempotente (teste); `compose-smoke` continua verde.
- [ ] F0-06 Endurecer o CI (pendências do ADR-003)
  - **Dependências:** F0-03. **Tamanho:** S. **Aceite:** erro de lint proposital em PR de teste derruba o job `gate` (critério 17 da F0-03, evidência anexada ao ADR); filtro de caminhos do smoke inclui `pyproject.toml` e `backend/tests/test_compose_smoke.py` (teste estático); após 3 execuções verdes seguidas do `compose-smoke`, registrar no ADR a decisão do humano sobre torná-lo check obrigatório.

## Fase 1: Fundação (plano, semanas 1-6)
Auditoria de licenças, modelo base, importadores e validação de datasets.
- [ ] F1-01 Registro de pesos e modelos em `docs/licencas.md`
  - **Dependências:** F0-04. **Tamanho:** S. **Aceite:** seção "Pesos e modelos" com campos origem (URL e commit/tag), SHA-256, licença SPDX, dataset de treino e data da verificação; teste falha se um arquivo de pesos versionado (`.pt`, `.pth`, `.onnx`, `.engine`, `.bin`) ou referenciado em configuração não tiver entrada.
- [ ] F1-02 Auditoria de licenças dos componentes candidatos e de seus pesos pré-treinados
  - **Dependências:** F1-01. **Tamanho:** M. **Aceite:** para YOLOX, RT-DETR, RF-DETR, SAM, ByteTrack, ONNX Runtime, OpenVINO, TensorRT, PyTorch e as bibliotecas de apoio previstas (decodificação de vídeo, avaliação de mAP), `docs/licencas.md` traz licença do código, licença dos pesos e do dataset de pré-treino, versão auditada e parecer PERMITIDO, PERMITIDO COM RESSALVA ou PROIBIDO; ADR registra os casos de ressalva. Pontos a verificar, sem presumir: SAM não é tratado como Apache-2.0 garantido; exigir a versão exata (tag/commit e variante, ex.: SAM, SAM 2 ou SAM 3) e a licença exata do código e dos pesos dessa versão, cada uma com URL e SHA-256 dos pesos, pois código e pesos podem ter licenças diferentes ou termos adicionais; tamanhos de modelo com licença diferente da do repositório (RF-DETR); TensorRT e CUDA são proprietários da NVIDIA (usar só como componente opcional do host do cliente, sem redistribuir); FFmpeg pode ser LGPL ou GPL conforme a build. Marcar a revisão por advogado como pendência antes do lançamento (o spec não é parecer jurídico).
- [ ] F1-03 Seleção do modelo base permissivo e da política de pesos iniciais [bloqueada: decisão D2]
  - **Dependências:** F1-02. **Tamanho:** S. **Aceite:** ADR com o modelo base escolhido, licença dos pesos, e a política (do zero ou ajuste a partir de pesos permissivos) conforme a decisão D2 registrada pelo humano; sem ela a tarefa não inicia.
- [ ] F1-04 Modelo de dados e migrações: projeto, dataset, imagem, anotação, classe
  - **Dependências:** F0-02, F0-04. **Tamanho:** M (dividir em esquema, migrações e repositório asyncpg). **Aceite:** migrações sobem e descem em Postgres real (marcador `docker`); restrições de integridade testadas (anotação sem imagem é rejeitada, classe única por projeto); ferramenta de migração e qualquer ORM registrados em `licencas.md` e ADR (psycopg continua proibido).
- [ ] F1-05 Upload de imagens para o armazenamento S3 (SeaweedFS) com limites e validação de tipo
  - **Dependências:** F1-04, F0-05. **Tamanho:** M. **Aceite:** arquivo acima do limite configurável retorna 413; tipo falso (extensão `.jpg` com conteúdo não imagem, checado por assinatura) retorna 415; imagem válida grava objeto no bucket e registro no banco com SHA-256; envio duplicado não cria segunda cópia.
- [ ] F1-06 Importador COCO (implementação própria)
  - **Dependências:** F1-04, F1-05. **Tamanho:** M. **Aceite:** fixture COCO pequena importa o número esperado de imagens, classes e caixas; arquivo com categoria ou imagem inexistente gera relatório de erro por item, sem importação parcial silenciosa.
- [ ] F1-07 Importador YOLO (formato de dados `.txt`, implementação própria)
  - **Dependências:** F1-04, F1-05. **Tamanho:** M. **Aceite:** fixture com `data.yaml` e rótulos normalizados importa caixas com coordenadas corretas (tolerância 1e-6); linhas inválidas são reportadas com arquivo e número da linha. A interface fala "formato YOLO (txt)" apenas como nome do formato.
- [ ] F1-08 Importador Pascal VOC (XML)
  - **Dependências:** F1-04, F1-05. **Tamanho:** M. **Aceite:** fixture VOC importa caixas e classes esperadas; XML malformado é rejeitado sem executar entidades externas (teste de XXE).
- [ ] F1-09 Exportação de anotações em COCO, YOLO (txt) e VOC
  - **Dependências:** F1-06, F1-07, F1-08. **Tamanho:** M. **Aceite:** teste de ida e volta (importar, exportar, importar) preserva imagens, classes e caixas nos três formatos.
- [ ] F1-10 Validação de datasets (relatório de qualidade)
  - **Dependências:** F1-04. **Tamanho:** M. **Aceite:** relatório JSON detecta, em fixtures com defeito plantado, imagem corrompida, caixa fora da imagem ou com área zero, duplicata por hash, classe sem exemplos e vazamento de imagem entre treino e validação; dataset limpo gera relatório sem erros.
- [ ] F1-11 Política de nomes: teste que bloqueia "yolo" e "ultralytics" em nomes de produto, modelos, artefatos e textos da interface
  - **Dependências:** F0-04. **Tamanho:** S. **Aceite:** teste varre nomes de pacotes, de modelos registrados e strings da UI e falha em ocorrência fora de uma allowlist explícita (YOLOX como componente, "formato YOLO", `docs/licencas.md`, comparativos e referências ao iNVR).
- [ ] F1-12 Declarar a licença do projeto (LICENSE, `pyproject.toml`, cabeçalhos) [bloqueada: decisão D1]
  - **Dependências:** F1-02. **Tamanho:** S. **Aceite:** arquivo `LICENSE` e campo de licença no `pyproject.toml` conforme D1; teste confere consistência entre eles e com `licencas.md`.
- [ ] F1-13 Refinar cronograma e esforço ao fim da Fase 1 [bloqueada: decisões D3 e D4]
  - **Dependências:** F1-10. **Tamanho:** S. **Aceite:** `docs/spec.md` ou ADR traz a estimativa revisada (pessoas-semana por fase) com a equipe e a data de início reais, e datas de cada gate.

## Fase 2: Treino e exportação (plano, semanas 7-14)
Gate da fase: modelo treinado e exportado reproduz o mAP do iNVR atual.
- [ ] F2-01 Núcleo de treino: carregador de dataset e aumentos de dados (implementação própria)
  - **Dependências:** F1-09. **Tamanho:** M. **Aceite:** carregador lê uma versão exportada e devolve tensores e caixas corretos (teste com fixture); aumentos são determinísticos com semente fixa; sem dependência da Ultralytics.
- [ ] F2-02a Adaptador YOLOX (Apache-2.0) com inicialização aleatória, sem pesos iniciais
  - **Dependências:** F2-01. **Tamanho:** M (o planejador subdivide se passar de ~1 dia). **Aceite:** o modelo YOLOX é construído com inicialização aleatória e semente fixa (mesma semente, mesmos pesos); forward em lote sintético devolve tensores com o formato esperado; nenhum arquivo de pesos é lido ou versionado. Atribuição e NOTICE do YOLOX em `licencas.md`. Não depende de D2.
- [ ] F2-02b Interface de treino, validação e predição do adaptador
  - **Dependências:** F2-02a. **Tamanho:** M. **Aceite:** treino de 2 épocas em dataset sintético minúsculo, em CPU, a partir de inicialização aleatória, termina e reduz a perda; checkpoint salvo é recarregável e produz as mesmas predições (tolerância numérica); validação e predição expostas pela mesma interface do núcleo. Não depende de D2.
- [ ] F2-02c Carregamento de pesos iniciais [bloqueada: decisão D2]
  - **Dependências:** F2-02b, F1-03. **Tamanho:** S. **Aceite:** o adaptador carrega pesos iniciais somente de origem registrada em `licencas.md` (F1-01); peso sem registro ou de licença não permissiva é recusado com mensagem clara (teste); a política (do zero ou ajuste) segue a decisão D2 registrada pelo humano; sem ela a tarefa não inicia.
- [ ] F2-03 CLI de treino, validação e predição (`inovalytics train|val|predict`)
  - **Dependências:** F2-02b. **Tamanho:** M. **Aceite:** os três subcomandos rodam sobre a fixture e retornam código 0; configuração inválida retorna código diferente de 0 com mensagem em português.
- [ ] F2-04 API Python de treino (biblioteca importável)
  - **Dependências:** F2-02b. **Tamanho:** M. **Aceite:** o exemplo mínimo da docstring é executado como teste e treina, valida e prediz; superfície pública tipada (mypy strict).
- [ ] F2-05 Métricas locais: mAP50, mAP50-95, precisão, revocação e curvas
  - **Dependências:** F2-02b. **Tamanho:** M. **Aceite:** em predições e rótulos sintéticos com mAP conhecido, o valor calculado difere do esperado em no máximo 1e-3; métricas por época são gravadas em arquivo e no banco. Se usar `pycocotools`, registrar a licença.
- [ ] F2-06 Receita de treino: do zero ou ajuste de pesos permissivos [bloqueada: decisão D2]
  - **Dependências:** F1-03, F2-02c, F2-03. **Tamanho:** M. **Aceite:** receita versionada (hiperparâmetros, aumentos, épocas) para o caminho escolhido em D2, com origem dos pesos registrada em `licencas.md` (F1-01).
- [ ] F2-07 Suporte a RT-DETR ou RF-DETR (segundo modelo base)
  - **Dependências:** F2-02b, F1-02. **Tamanho:** L. **Aceite:** o mesmo dataset de fixture treina com o segundo modelo atrás da mesma interface do núcleo; só tamanhos com licença permissiva confirmada na F1-02 são habilitados (teste lê a allowlist).
- [ ] F2-08 Exportação ONNX com verificação numérica
  - **Dependências:** F2-02b. **Tamanho:** M. **Aceite:** o modelo exportado roda no ONNX Runtime e difere do PyTorch em no máximo 1e-3 (tolerância configurável) nas saídas da fixture; metadados (classes, tamanho de entrada, hash dos pesos de origem) gravados no arquivo.
- [ ] F2-09 Exportação OpenVINO
  - **Dependências:** F2-08. **Tamanho:** M. **Aceite:** o IR gerado roda em CPU via OpenVINO e produz as mesmas detecções do ONNX (IoU >= 0,99 nas caixas da fixture).
- [ ] F2-10 Exportação TensorRT (opcional, host NVIDIA)
  - **Dependências:** F2-08, F1-02. **Tamanho:** M. **Aceite:** teste marcado `gpu` (pulado sem GPU) gera o engine e compara com o ONNX; sem TensorRT instalado, o comando falha com mensagem clara e sem quebrar o resto. TensorRT nunca entra na imagem base nem no `pyproject.toml` principal (proprietário, ver F1-02).
- [ ] F2-11 Quantização INT8 com calibração
  - **Dependências:** F2-09, F2-05. **Tamanho:** L (dividir por backend: ONNX Runtime, OpenVINO, TensorRT). **Aceite:** modelo INT8 do dataset de fixture fica dentro de uma queda máxima de mAP configurável (valor definido no ADR) em relação ao FP32 e é menor em disco; sem conjunto de calibração, o comando recusa.
- [ ] F2-12 Matriz de hardware do cliente
  - **Dependências:** F2-09, F2-10. **Tamanho:** M. **Aceite:** documento e detecção automática (`inovalytics doctor`) classificam o host em CPU, iGPU Intel (OpenVINO) ou GPU NVIDIA e escolhem o backend; testes unitários com hardware simulado cobrem os perfis, incluindo "sem GPU"; requisitos mínimos por perfil publicados em `docs/`.
- [ ] F2-13 Custo de GPU e dimensionamento de treino
  - **Dependências:** F2-02b. **Tamanho:** M. **Aceite:** relatório medido (tempo por época, pico de VRAM, imagens/s) por tamanho de modelo e resolução; treino retomável a partir de checkpoint (teste interrompe e retoma sem perder mais de 1 época); mixed precision e tamanho de lote configuráveis.
- [ ] F2-14 Harness de benchmark contra o iNVR (mAP, latência, FPS)
  - **Dependências:** F2-05, F2-08. **Tamanho:** M. **Aceite:** um comando roda um modelo ONNX do iNVR (caixa-preta, via ONNX Runtime) e um modelo do Inovalytics no mesmo conjunto de validação e gera relatório comparável com mAP, latência p50/p95 e FPS no mesmo hardware; roda em CI com fixture minúscula e limiar configurável. Depende de acesso ao conjunto de referência do iNVR (dependência externa).
- [ ] F2-15 GATE da Fase 2: modelo treinado e exportado reproduz o mAP do iNVR atual [bloqueada: decisão D2]
  - **Dependências:** F2-06, F2-09, F2-14. **Tamanho:** L. **Nota:** a quantização INT8 (F2-11) não faz parte do gate; fica como meta pós-gate. **Aceite:** relatório do F2-14 mostra mAP50-95 do modelo Inovalytics (exportado em ONNX e em OpenVINO) >= mAP do iNVR menos a tolerância fixada em ADR antes da execução, no conjunto de EPI de referência. Se falhar, abrir tarefa de troca do modelo base (F2-07) em vez de reduzir a tolerância.

## Fase 3: Anotação (plano, semanas 9-14; sobrepõe a Fase 2)
- [ ] F3-01 Base do front em React (shell, rotas, cliente da API, i18n pt-BR)
  - **Dependências:** F0-01. **Tamanho:** M. **Aceite:** `make check` roda eslint e vitest sobre a base; teste de componente confere textos em português; React, roteador e demais pacotes registrados em `licencas.md`.
- [ ] F3-02 Editor de anotação de caixas (desenhar, mover, redimensionar, zoom, atalhos, desfazer)
  - **Dependências:** F3-01, F1-04, F1-05. **Tamanho:** L. **Aceite:** testes de componente cobrem criar, editar e desfazer caixas e a persistência pela API; coordenadas salvas batem com as exibidas em zoom diferente de 100%.
- [ ] F3-03 Serviço de segmentação SAM (licença a confirmar na F1-02) em contêiner
  - **Dependências:** F1-02, F0-02. **Tamanho:** M. **Aceite:** endpoint recebe imagem e ponto ou caixa e devolve máscara e caixa (teste com modelo fake no CI; teste real marcado `gpu`/`slow`); licença do SAM a confirmar (F1-02): o serviço só é habilitado com versão e licença de código e pesos aprovadas na F1-02; pesos do SAM registrados em `licencas.md` (F1-01) e não versionados no Git.
- [ ] F3-04 Auto-rotulagem com SAM na interface (clique para anotar, lote por classe)
  - **Dependências:** F3-02, F3-03. **Tamanho:** M. **Aceite:** com o serviço simulado, clicar num objeto cria uma anotação marcada como "automática"; o lote anota N imagens e nenhuma anotação automática vira "aprovada" sem ação humana.
- [ ] F3-05 Fluxo de revisão de anotações
  - **Dependências:** F3-02. **Tamanho:** M. **Aceite:** estados (pendente, automática, revisada, aprovada) com transições válidas testadas; transição inválida retorna 409; histórico registra quem e quando.
- [ ] F3-06 Versionamento de datasets (versões imutáveis)
  - **Dependências:** F1-09, F3-05. **Tamanho:** L. **Aceite:** criar versão gera manifesto com hashes e divisão treino/validação/teste determinística (mesma semente, mesmo resultado); editar o dataset depois não altera a versão; diff entre duas versões lista imagens e anotações adicionadas, removidas e alteradas.
- [ ] F3-07 Ligar versão de dataset ao treino
  - **Dependências:** F3-06, F2-03. **Tamanho:** S. **Aceite:** `inovalytics train --dataset-version <id>` treina sobre a versão exata e grava o ID da versão nas métricas e no modelo.

## Fase 4: Plataforma local (plano, semanas 13-20)
Gate da fase: beta fechado com clientes do iNVR.
- [ ] F4-01 Autenticação, perfis e auditoria
  - **Dependências:** F1-04. **Tamanho:** L. **Aceite:** rotas protegidas retornam 401/403 conforme o perfil (administrador, anotador, revisor, leitor) em testes parametrizados; senhas com hash forte (nunca em log); toda ação de escrita gera registro de auditoria.
- [ ] F4-02 Painel de projetos, datasets, treinos e modelos
  - **Dependências:** F3-01, F4-01. **Tamanho:** L. **Aceite:** testes de componente cobrem listagem, criação e navegação entre as quatro entidades, com estados de carregamento e erro em português.
- [ ] F4-03 Fila de treino em GPU local
  - **Dependências:** F2-03, F2-13, F4-01. **Tamanho:** L. **Aceite:** com GPU simulada, jobs executam um por GPU, em ordem de prioridade; cancelar e retomar funcionam; job interrompido por queda do worker é recolocado na fila; logs chegam em streaming; sem GPU, o job roda em CPU com aviso explícito.
- [ ] F4-04 Painel de métricas de treino (curvas, comparação entre execuções)
  - **Dependências:** F2-05, F4-02. **Tamanho:** M. **Aceite:** dado um histórico de métricas fixo, o gráfico renderiza as séries esperadas (teste de componente); comparar duas execuções mostra a diferença de mAP.
- [ ] F4-05 Registro de modelos
  - **Dependências:** F2-08, F4-02. **Tamanho:** M. **Aceite:** cada modelo guarda versão, formatos exportados, métricas, versão do dataset e origem/licença dos pesos; modelo sem origem registrada não pode ser promovido para implantação.
- [ ] F4-06 API REST pública versionada (`/v1`) com tokens de API
  - **Dependências:** F4-01. **Tamanho:** M. **Aceite:** OpenAPI gerado cobre todos os recursos; teste de contrato impede quebra de rota existente; token revogado retorna 401.
- [ ] F4-07 Servidor de inferência em Docker (REST, CPU/OpenVINO/GPU)
  - **Dependências:** F2-09, F2-12, F4-05. **Tamanho:** L. **Aceite:** imagem sobe com `docker compose up`, carrega um modelo do registro e responde `POST /v1/infer` com detecções iguais às do CLI na fixture; healthcheck e perfil por hardware; imagens de base (inclusive CUDA, se usada) registradas em `licencas.md`.
- [ ] F4-08 Instalador on-premise (Compose de produção, TLS, backup e restauração, atualização)
  - **Dependências:** F4-07, F4-01. **Tamanho:** L. **Aceite:** instalação limpa em máquina virtual sobe a stack sem editar arquivos além do `.env`; backup seguido de restauração recupera banco e objetos; atualização preserva dados (verificado no `compose-smoke` estendido). Registrar obrigações de redistribuição das imagens incluídas.
- [ ] F4-09 Política de armazenamento e retenção (uso de disco, limpeza de checkpoints)
  - **Dependências:** F4-03. **Tamanho:** S. **Aceite:** limite configurável de disco por projeto; limpeza remove apenas checkpoints intermediários (teste confere que modelos registrados permanecem).
- [ ] F4-10 Preparação do beta fechado (termos, canal de feedback, telemetria desligada por padrão) [bloqueada: decisões D1 e D5]
  - **Dependências:** F4-08. **Tamanho:** M. **Aceite:** termos de uso e distribuição do beta conforme D1 e D5; lista de clientes e número mínimo de participantes definidos pelo humano e registrados em ADR; telemetria, se existir, vem desligada e é testada como tal.
- [ ] F4-11 GATE da Fase 4: beta fechado com clientes do iNVR [bloqueada: decisão D4]
  - **Dependências:** F4-10, F2-15. **Tamanho:** L. **Aceite:** o número de clientes definido na F4-10 instala e usa a plataforma sem intervenção da equipe de desenvolvimento, treina e implanta um modelo, e entrega relatório de feedback; defeitos críticos abertos = 0 (critérios finais fixados no ADR da F4-10).

## Fase 5: Soluções de vídeo (plano, semanas 15-24, contínuo)
- [ ] F5-01 Ingestão RTSP com reconexão
  - **Dependências:** F0-02, F1-02. **Tamanho:** M. **Aceite:** com servidor RTSP simulado, o leitor reconecta após queda em até o prazo configurado e não vaza memória em 10 min de teste; biblioteca de decodificação escolhida com licença e build (LGPL vs GPL do FFmpeg) registradas em ADR.
- [ ] F5-02 Pipeline de inferência em fluxo contínuo
  - **Dependências:** F5-01, F2-09, F4-07. **Tamanho:** M. **Aceite:** fluxo de vídeo de teste gera detecções por quadro com o modelo ONNX/OpenVINO; FPS e latência expostos como métricas; descarte de quadros sob carga é contabilizado.
- [ ] F5-03 Contrato de módulos de analítico (plugin: modelo, pós-processamento, eventos)
  - **Dependências:** F5-02. **Tamanho:** S. **Aceite:** módulo de exemplo registrado por interface tipada é carregado e executado pelo servidor; módulo malformado é rejeitado com erro claro.
- [ ] F5-04 Rastreamento ByteTrack (MIT)
  - **Dependências:** F5-02, F1-02. **Tamanho:** M. **Aceite:** em sequência sintética com trajetórias conhecidas, IDs se mantêm (taxa de troca de ID abaixo do limiar definido no teste); aviso de licença MIT preservado em `licencas.md` e `NOTICE`.
- [ ] F5-05 Zonas (polígonos e linhas) e editor de zonas
  - **Dependências:** F5-04, F3-01. **Tamanho:** M. **Aceite:** teste geométrico decide corretamente "dentro/fora" e "cruzou a linha" em casos limite (ponto na borda, polígono côncavo); zonas são salvas por câmera e carregadas pelo servidor.
- [ ] F5-06 Contagem (entrada/saída por linha, ocupação por zona)
  - **Dependências:** F5-05. **Tamanho:** M. **Aceite:** em vídeo/trajetórias sintéticas com N cruzamentos conhecidos, a contagem é exatamente N nos dois sentidos; reinício do serviço não zera nem duplica contadores já persistidos.
- [ ] F5-07 Mapa de calor
  - **Dependências:** F5-04. **Tamanho:** M. **Aceite:** acumulação em trajetórias sintéticas produz matriz esperada; API devolve imagem de sobreposição para a janela de tempo pedida.
- [ ] F5-08 Regras e alertas (condições, cooldown, agenda)
  - **Dependências:** F5-05, F5-06. **Tamanho:** M. **Aceite:** regra "pessoa na zona por mais de X s" dispara uma vez por janela de cooldown e respeita horário configurado (testes com relógio simulado).
- [ ] F5-09 Webhooks assinados com repetição
  - **Dependências:** F5-08. **Tamanho:** M. **Aceite:** carga assinada com HMAC-SHA256 verificável; falhas do destino geram novas tentativas com recuo exponencial e ficam registradas; fila persiste a reinício; segredos nunca aparecem em log.
- [ ] F5-10 Conector RTSP genérico (cadastro de câmeras)
  - **Dependências:** F5-01, F4-01. **Tamanho:** M. **Aceite:** URL e credenciais cadastradas (credenciais cifradas em repouso com chave fora do banco e fora do Git, lida de variável de ambiente `.env`/segredo, nunca devolvidas pela API nem gravadas em log; teste confere que o valor armazenado não contém a senha em texto claro e que a decifragem funciona com a chave certa e falha com a errada; a biblioteca de criptografia escolhida, com licença permissiva, é registrada em `docs/licencas.md` e em ADR); botão/endpoint "testar conexão" retorna sucesso ou motivo da falha.
- [ ] F5-11 Conector iNVR/NVR (eventos e prints com data/hora; Hikvision pelo protocolo HTTP/ISAPI)
  - **Dependências:** F5-08, F5-10. **Tamanho:** M. **Aceite:** com NVR simulado, um evento gera print com data/hora do fuso correto e chega ao destino configurado; não usar SDKs proprietários da Hikvision (apenas HTTP documentado); qualquer SDK vira ADR e entrada em `licencas.md`.
- [ ] F5-12 Migração do analítico de EPI
  - **Dependências:** F5-03, F2-15. **Tamanho:** M. **Aceite:** módulo EPI roda no servidor e, no conjunto de referência, seu mAP/recall não fica abaixo do analítico atual dentro da tolerância do ADR; a origem dos pesos está em `licencas.md` e, se for desconhecida ou derivada da Ultralytics, o módulo é bloqueado até retreino com base permissiva.
- [ ] F5-13 Migração do analítico de fogo
  - **Dependências:** F5-03, F2-15. **Tamanho:** M. **Aceite:** mesmos critérios da F5-12 aplicados ao conjunto de fogo, incluindo taxa de falso positivo máxima definida no ADR.
- [ ] F5-14 Migração da busca de pessoas em gravações de NVR
  - **Dependências:** F5-03, F5-11. **Tamanho:** M. **Aceite:** a busca em uma gravação de teste devolve prints com data/hora dos quadros em que há pessoa, com recall igual ou superior ao da aplicação atual no conjunto de referência; registro de acesso às buscas (auditoria, F4-01) e nota de conformidade com a LGPD na documentação.
- [ ] F5-15 Teste de carga e estabilidade por perfil de hardware
  - **Dependências:** F5-06, F5-09, F2-12. **Tamanho:** M. **Aceite:** relatório com número de câmeras simultâneas sustentadas por perfil (CPU, iGPU, NVIDIA) e execução contínua de 24 h sem crescimento de memória acima do limiar definido; resultados alimentam os requisitos de hardware da documentação.

## Fase 6: Lançamento (plano, semanas 21-24)
- [ ] F6-01 Documentação em português (instalação, tutoriais, referência de CLI e API)
  - **Dependências:** F4-08, F4-06. **Tamanho:** L. **Aceite:** site de documentação compila no CI sem avisos e sem links quebrados; o passo a passo de instalação foi executado por pessoa de fora da equipe; gerador de docs registrado em `licencas.md`.
- [ ] F6-02 Notebooks de exemplo
  - **Dependências:** F2-04, F3-07. **Tamanho:** M. **Aceite:** pelo menos 3 notebooks (importar dataset, treinar e exportar, inferir em vídeo) executam do início ao fim em CI com dados mínimos; Jupyter e dependências registrados.
- [ ] F6-03 Comparativo de desempenho
  - **Dependências:** F2-14, F5-15. **Tamanho:** M. **Aceite:** relatório reproduzível (scripts, hardware, versões) compara Inovalytics e iNVR atual em mAP, latência e FPS; comparações com a Ultralytics usam apenas números públicos citados com fonte, sem instalar ou executar software dela dentro do repositório.
- [ ] F6-04 Identidade própria (nome de produto e modelos, logotipo, paleta)
  - **Dependências:** F1-11. **Tamanho:** M. **Aceite:** nomes de produto e de modelos passam no teste da F1-11; busca de anterioridade de marca registrada no ADR; recursos gráficos com autoria ou licença próprias.
- [ ] F6-05 Auditoria final de licenças da versão (SBOM, NOTICE, imagens, revisão jurídica) [bloqueada: decisão D1]
  - **Dependências:** F0-04, F1-02, F4-08. **Tamanho:** M. **Aceite:** SBOM gerado e sem componente proibido; `NOTICE` completo; pareceres de ressalva da F1-02 resolvidos; revisão por advogado registrada em ADR.
- [ ] F6-06 Publicação e distribuição conforme a licença escolhida [bloqueada: decisão D1]
  - **Dependências:** F6-05, F1-12. **Tamanho:** S. **Aceite:** artefatos publicados (repositório e/ou imagens) com a licença definida em D1 e checagem de coerência entre `LICENSE`, `pyproject.toml` e imagens.
- [ ] F6-07 Canais e política de suporte [bloqueada: decisão D5]
  - **Dependências:** F6-01. **Tamanho:** S. **Aceite:** página de suporte e política publicadas, coerentes com o modelo de negócio escolhido em D5 (gratuito total ou núcleo gratuito com suporte pago).
- [ ] F6-08 Release 1.0 e checklist de lançamento [bloqueada: decisões D1 e D4]
  - **Dependências:** F6-01, F6-02, F6-03, F6-05, F6-06, F4-11. **Tamanho:** M. **Aceite:** checklist com todos os itens marcados; `make check` verde na tag; data de lançamento registrada conforme a data de início real (D4). Inclui o checklist recorrente de revisão de licenças, repetido a cada release (não só na 1.0): `make check` (verificação da F0-04) verde; dependências, imagens, pesos e datasets novos desde o último release presentes em `docs/licencas.md`; pareceres da F1-02 revalidados para versões atualizadas; SBOM e `NOTICE` regenerados e sem componente proibido; ausência de Ultralytics e de marca proibida (F1-11); resultado registrado em ADR ou nas notas do release.

## Tarefas bloqueadas por decisão humana (resumo)
- D1 (código aberto ou fechado): F1-12, F4-10, F6-05, F6-06, F6-08.
- D2 (do zero ou ajuste de pesos): F1-03, F2-02c, F2-06, F2-15; F2-02a e F2-02b avançam com inicialização aleatória.
- D3 (equipe): F1-13.
- D4 (data de início): F1-13, F4-11, F6-08.
- D5 (modelo de negócio): F4-10, F6-07.

## Riscos de planejamento
1. **Escopo grande para equipe pequena:** cerca de 80 pessoas-semana com 2 a 3 pessoas (D3 ainda aberta) e fases sobrepostas; mitigação: respeitar a prioridade do spec (Fases 1, 2 e 5) e deixar Fases 3 e 4 como folga de cronograma, replanejando em F1-13.
2. **Gates dependentes de clientes e dados do iNVR:** F2-14, F2-15, F4-11 e as migrações da Fase 5 exigem o conjunto de referência, os modelos atuais e clientes dispostos ao beta; sem acesso garantido, o cronograma trava; mitigação: pedir esses insumos já na Fase 1.
3. **Decisão D2 no caminho crítico:** sem ela, a receita de treino e o gate de mAP da Fase 2 não começam; se o gate falhar, a troca do modelo base (F2-07) consome semanas não previstas.
4. **Licenças de origem incerta:** modelos atuais do iNVR podem derivar de pesos da Ultralytics, alguns tamanhos de RF-DETR podem ter licença distinta, e TensorRT/CUDA/FFmpeg não são permissivos por padrão; a F1-02 deve rodar cedo e a F5-12 a F5-14 só avançam com origem comprovada.
5. **Estimativa sem base e infraestrutura de teste limitada:** o esforço é de plano pré-Fase 1 e o início de 02/11/2026 (D4) não está confirmado; o CI hospedado não tem GPU, então TensorRT, INT8 e a fila de treino ficam com testes manuais ou simulados, com risco de defeitos só aparecerem no beta.

