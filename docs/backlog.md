# Backlog Inovalytics

Status: [ ] pendente · [~] em andamento · [x] concluída

## Fase 0: Fundação
- [ ] F0-01 Estrutura do repositório (backend/, frontend/, docs/, docker/), `pyproject.toml`, ruff, mypy, pytest
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
