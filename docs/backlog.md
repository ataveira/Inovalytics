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
- [ ] F0-02 `docker-compose.yml` com PostgreSQL, MinIO e API FastAPI (healthcheck)
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
