# Inovalytics: especificação e plano de desenvolvimento

> Fonte de verdade que os agentes consultam. Plano original de 21/10/2026 (documento "Inovalytics — Plano de Desenvolvimento"), com o estado de implementação e as decisões já tomadas na seção "Estado de implementação e decisões tomadas".

## Visão e escopo

O Inovalytics será uma plataforma gratuita e local de visão computacional: anotar, treinar, exportar e rodar modelos de detecção em vídeo, sem depender de código AGPL da Ultralytics.

A [Ultralytics Platform](https://www.businesswire.com/news/home/20260318015028/en) cobre anotação com SAM, treino em nuvem ou em hardware local e deploy em 43 regiões. O [plano gratuito dela](https://community.ultralytics.com/t/introducing-ultralytics-platform-annotate-train-export-and-deploy-all-from-your-browser/1876) já inclui 100 GB, auto-anotação com SAM3, 3 deploys e exportação para mais de 17 formatos. Por isso, "ser grátis" não basta como diferencial.

Diferenciais propostos:

- Roda 100% on-premise, sem enviar dados para nuvem de terceiros.
- Interface e documentação em português.
- Integração nativa com NVRs e RTSP (iNVR, Hikvision).
- Analíticos de vídeo prontos: EPI, fogo, busca de pessoas, contagem e zonas.
- Licença permissiva, sem a obrigação de abrir o código do produto do cliente.

## Licenças

O software e os modelos da Ultralytics saem sob [AGPL-3.0 ou Licença Enterprise](https://github.com/ultralytics/yolo26); copiar código ou pesos deles obriga o Inovalytics a ser AGPL ou a pagar licença comercial.

Regras do projeto:

- Não importar o pacote `ultralytics`, nem copiar código ou pesos YOLOv5/v8/11/26.
- Reimplementar as funções (treino, exportação, rastreamento) a partir de componentes permissivos.
- Não usar "YOLO" nem a marca Ultralytics no nome de produto ou modelos.
- Auditoria de licença de cada dependência e de cada peso pré-treinado antes de entrar no repositório.

Candidatos permissivos, a confirmar na auditoria: YOLOX (já usado no iNVR), RT-DETR ou RF-DETR, SAM (Apache 2.0), ByteTrack (MIT) e ONNX Runtime. Esta seção não é parecer jurídico; vale validar com advogado antes do lançamento.

## Arquitetura

Cinco módulos, todos em contêineres Docker e comunicando-se por API REST:

1. **Núcleo de treino:** CLI e biblioteca Python para treino, validação e predição, com modelo base permissivo.
2. **Exportação:** ONNX, TensorRT e OpenVINO, com quantização INT8.
3. **Anotação:** interface web com auto-rotulagem via SAM, revisão e versionamento de datasets.
4. **Plataforma local:** painel de projetos, datasets, treinos e modelos; fila de treino em GPU local e métricas.
5. **Soluções de vídeo:** servidor de inferência RTSP/NVR com rastreamento, zonas, contagem, mapa de calor, alertas e webhooks; os analíticos de EPI, fogo e pessoas entram como módulos.

## Fases e cronograma

O projeto leva cerca de 24 semanas até o lançamento, com beta na semana 20. Premissa: início em 02/11/2026; as fases 2–3 e 4–5 se sobrepõem.

1. **Fundação (semanas 1–6):** auditoria de licenças, modelo base, importadores COCO/YOLO/VOC e validação de datasets.
2. **Treino e exportação (semanas 7–14):** CLI e API Python, exportação ONNX/TensorRT/OpenVINO, métricas locais. Gate: modelo treinado e exportado reproduz o mAP do iNVR atual.
3. **Anotação (semanas 9–14):** interface web com SAM, revisão e versionamento.
4. **Plataforma local (semanas 13–20):** painel, fila de treino, API REST e servidor de inferência em Docker. Gate: beta fechado com clientes do iNVR.
5. **Soluções de vídeo (semanas 15–24, contínuo):** rastreamento, zonas, contagem, heatmap, alertas, conectores NVR/RTSP e migração dos analíticos existentes.
6. **Lançamento (semanas 21–24):** documentação em português, notebooks de exemplo, comparativo de desempenho e identidade própria.

## Estimativa de esforço

O esforço total estimado é de cerca de 80 pessoas-semana, com equipe de 3 pessoas no pico. Valores aproximados, a refinar após a Fase 1.

| Fase | Equipe | Duração (semanas) | Esforço (pessoas-semana) |
| --- | --- | --- | --- |
| 1. Fundação | 2 | 6 | 10 |
| 2. Treino e exportação | 2 | 8 | 14 |
| 3. Anotação | 2 | 6 | 10 |
| 4. Plataforma local | 3 | 8 | 21 |
| 5. Soluções de vídeo | 2 | 10 | 20 |
| 6. Lançamento | 2 | 4 | 6 |

Perfis necessários: engenheiro de ML/visão computacional, desenvolvedor backend (Python/API), desenvolvedor frontend (anotação e painel) e apoio de DevOps para Docker e GPU.

## Riscos e mitigações

| Risco | Mitigação |
| --- | --- |
| Contaminação por AGPL (código ou pesos copiados) | Auditoria de licenças na Fase 1 e revisão de cada dependência a cada release |
| Precisão abaixo dos modelos YOLO atuais | Benchmark contra o iNVR desde a Fase 2; trocar o modelo base se faltar mAP |
| Concorrência com o plano gratuito da Ultralytics | Focar em on-premise, português e integração com NVR, onde ela não atua |
| Escopo grande para equipe pequena | Entregar por fases com gates; priorizar Fases 1, 2 e 5 |
| Custo de GPU para treino | Fila local e suporte a hardware do cliente |

## Estado de implementação e decisões tomadas (atualizado em 28/10/2026)

Decisões já fechadas desde o plano original (registradas em `docs/decisoes.md`):

- **Armazenamento de arquivos:** SeaweedFS (Apache-2.0), acessado por API S3 com boto3 e variáveis `S3_*`. MinIO e Garage (AGPL-3.0) ficaram de fora (ADR-002).
- **Driver do PostgreSQL:** asyncpg (Apache-2.0). psycopg (LGPL-3.0) ficou de fora (ADR-002).
- **Exceção de licença aprovada:** certifi (MPL-2.0), dependência transitiva usada só em desenvolvimento, sem modificação (ADR-002).
- **Exposição da API:** porta publicada apenas em `127.0.0.1` por padrão; abrir para a rede exige `API_BIND` explícito.
- **CI:** GitHub Actions com `gate` (Python 3.11 e 3.12, `make check`) e `compose-smoke` (ADR-003). O `Makefile` é a fonte da verdade e o CI é portável.
- **Proteção do `main`:** ruleset exigindo `gate (python 3.11)`, `gate (python 3.12)` e `compose-smoke`, sem force push e sem exclusão.

Já entregue (repositório `ataveira/Inovalytics`, branch `main`):

- F0-01: estrutura do repositório e gate de qualidade (`make check`).
- F0-02: Docker Compose com PostgreSQL, SeaweedFS e API FastAPI (`/health`, `/health/ready`).
- F0-03: CI no GitHub Actions.

### Alinhamento entre o plano e o backlog

O `docs/backlog.md` usa fases técnicas próprias (F0 a F4). A correspondência com as fases deste plano é:

| Backlog | Plano |
| --- | --- |
| F0: infraestrutura técnica (repositório, Compose, CI, licenças) | Pré-requisito da Fase 1 (Fundação) |
| F1: dados e anotação | Fase 1 (importadores, validação) e Fase 3 (anotação) |
| F2: treino | Fase 2 (treino e exportação) |
| F3: exportação e deploy | Fases 2 e 5 (exportação, servidor de inferência, integração NVR) |
| F4: produto | Fases 4 e 6 (plataforma local e lançamento) |

O backlog ainda não cobre todos os itens do plano (por exemplo: auditoria de licenças dos pesos pré-treinados, benchmark contra o iNVR, zonas, contagem, mapa de calor, alertas e webhooks, migração dos analíticos de EPI, fogo e busca de pessoas). Revisar o backlog contra esta especificação é a próxima tarefa de planejamento.

### Observação sobre os módulos

Os cinco módulos acima (núcleo de treino, exportação, anotação, plataforma local, soluções de vídeo) são a divisão de referência. O `CLAUDE.md` aponta para esta seção.

## Decisões pendentes

- [ ] Código aberto ou fechado, e sob qual licença (Apache 2.0, MIT ou outra).
- [ ] Modelos treinados do zero ou ajustados a partir de pesos permissivos.
- [ ] Tamanho e composição da equipe (a estimativa supõe 2–3 pessoas).
- [ ] Data de início real (o cronograma assume 02/11/2026).
- [ ] Modelo de negócio: gratuito total ou núcleo gratuito com suporte pago.
