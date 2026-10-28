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
- [~] F0-02 `docker-compose.yml` com PostgreSQL, armazenamento S3 e API FastAPI (healthcheck)
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
- [ ] F0-03 CI no GitHub Actions rodando `make check`
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
