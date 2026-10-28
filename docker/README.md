# docker

- `api/Dockerfile`: imagem da API FastAPI (usuário não root, healthcheck).
- `../docker-compose.yml`: serviços `db` (PostgreSQL), `storage` (SeaweedFS, gateway S3) e `api`.

Uso: `cp .env.example .env`, preencher `POSTGRES_PASSWORD`, `S3_ACCESS_KEY` e `S3_SECRET_KEY`,
depois `docker compose up -d --wait`. Só a API é publicada no host (`API_PORT`, padrão 8000).
Teste de ponta a ponta (exige Docker): `make compose-smoke`.

Atenção: a porta da API é publicada em `0.0.0.0` por padrão (`${API_PORT:-8000}:8000`), ou seja,
acessível a toda a rede da máquina. Para restringir ao próprio host, troque o mapeamento em
`docker-compose.yml` para `127.0.0.1:${API_PORT:-8000}:8000` (bind em 127.0.0.1) e use `API_PORT`
para mudar a porta.
