# Kit de agentes: Inovalytics

## Como usar
1. Crie o repositório e copie esta pasta para a raiz.
2. Cole o plano de desenvolvimento em `docs/spec.md`.
3. Instale o Claude Code e, na raiz do projeto, rode `claude`.
4. Execute `/ciclo` (ou `/ciclo F0-01`) e acompanhe.

## O que tem aqui
- `CLAUDE.md`: regras do projeto (licenciamento, stack, fluxo).
- `.claude/agents/`: planejador, implementador, testador, revisor, corretor.
- `.claude/commands/ciclo.md`: comando que orquestra o ciclo.
- `.claude/settings.json`: hook de lint após cada edição e bloqueios.
- `Makefile`: `make check` é o gate de qualidade (inclui bloqueio de `ultralytics`).
- `docs/backlog.md`: tarefas por fase.

## Próximos passos
- Rodar a F0-01 e ajustar o `Makefile` à estrutura criada.
- Adicionar GitHub Actions para rodar o ciclo em pull requests.
- Para orquestração por código, migrar o fluxo para o Agent SDK.

## Desenvolvimento
```
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
make check   # ruff, mypy, pytest, eslint, vitest
```
Copie `.env.example` para `.env` (nunca commitar). Layout e ferramentas: `docs/decisoes.md`.

## Subir com Docker Compose

Ver `docker/README.md`. Atenção: a porta da API é publicada em `0.0.0.0` por padrão; para
restringir, use bind em `127.0.0.1` no mapeamento de portas e `API_PORT` para trocar a porta.
