# Backlog Inovalytics

Status: [ ] pendente · [~] em andamento · [x] concluída

## Fase 0: Fundação
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
- [~] F0-03 CI no GitHub Actions rodando `make check`
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
- [ ] F0-04 `docs/licencas.md` e verificação automática de licenças das dependências

## Fase 1: Dados e anotação
- [ ] F1-01 Modelo de dados: projeto, dataset, imagem, anotação, classe
- [ ] F1-02 Upload de imagens para MinIO com limites e validação de tipo
- [ ] F1-03 Importação/exportação de anotações nos formatos COCO e YOLO (implementação própria)
- [ ] F1-04 Interface web de anotação (caixas) em português
- [ ] F1-05 Pré-anotação assistida com SAM (Apache-2.0)

## Fase 2: Treino
- [ ] F2-01 Worker de treino com YOLOX (fila + logs + métricas)
- [ ] F2-02 Suporte a RT-DETR/RF-DETR
- [ ] F2-03 Painel de métricas (mAP, perda, curvas)

## Fase 3: Exportação e deploy
- [ ] F3-01 Exportação ONNX e OpenVINO
- [ ] F3-02 Serviço de inferência por RTSP com ByteTrack (MIT)
- [ ] F3-03 Integração com o iNVR (eventos e prints com data/hora)

## Fase 4: Produto
- [ ] F4-01 Autenticação, perfis e auditoria
- [ ] F4-02 Instalador on-premise e documentação em português
