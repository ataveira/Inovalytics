# docker

- `api/Dockerfile`: imagem da API FastAPI (usuário não root, healthcheck).
- `../docker-compose.yml`: serviços `db` (PostgreSQL), `storage` (SeaweedFS, gateway S3) e `api`.

Uso: `cp .env.example .env`, preencher `POSTGRES_PASSWORD`, `S3_ACCESS_KEY` e `S3_SECRET_KEY`,
depois `docker compose up -d --wait`. Só a API é publicada no host (`API_PORT`, padrão 8000).
Teste de ponta a ponta (exige Docker): `make compose-smoke`.
