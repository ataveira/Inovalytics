# Inovalytics

Plataforma gratuita e on-premise de visão computacional (anotação, treino, exportação e deploy de modelos de detecção), em português, integrada ao iNVR/NVR via RTSP. Alternativa à Ultralytics Platform.

## Regra nº 1: licenciamento
- NUNCA copiar código, pesos ou arquivos da Ultralytics (AGPL-3.0). Nada de `import ultralytics`.
- Usar somente componentes permissivos: YOLOX (Apache-2.0), RT-DETR/RF-DETR (Apache-2.0), SAM (Apache-2.0), ByteTrack (MIT), ONNX Runtime, OpenVINO, PyTorch.
- Toda dependência nova exige verificação de licença registrada em `docs/licencas.md`.
- O revisor deve bloquear qualquer mudança que viole esta regra.

## Arquitetura (5 módulos Docker)
Detalhes em `docs/spec.md`. Módulos: dados/anotação, treino, exportação, inferência/integração NVR, interface web.

## Stack padrão
- Python 3.11+, FastAPI, PostgreSQL, MinIO, Docker Compose, React no front.
- Testes: pytest. Lint: ruff. Tipos: mypy. Front: vitest + eslint.

## Comandos
- Testes: `make test`
- Lint: `make lint`
- Gate de qualidade completo: `make check`

## Fluxo de trabalho dos agentes
1. `planejador` lê `docs/backlog.md`, escolhe a próxima tarefa e escreve critérios de aceite.
2. `implementador` cria branch `feat/<id>` e implementa com testes.
3. `testador` roda `make check` e cria os testes que faltam.
4. `revisor` analisa o diff contra a spec, licenças e segurança; devolve APROVADO ou lista de problemas.
5. `corretor` resolve os problemas apontados. Máximo de 3 ciclos; depois, escalar para o humano.
6. Tarefa só é concluída com `make check` verde e revisor APROVADO.

Atalho: `/ciclo` executa esse fluxo para a próxima tarefa do backlog.

## Convenções
- Código e identificadores em inglês; documentação e interface em português do Brasil.
- Commits pequenos, no formato `tipo(escopo): descrição`.
- Nunca commitar credenciais; usar `.env` (fora do Git).
- Decisões relevantes vão para `docs/decisoes.md` (ADR curto).
