# docker

- `api/Dockerfile`: imagem da API FastAPI (usuário não root, healthcheck).
- `../docker-compose.yml`: serviços `db` (PostgreSQL), `storage` (SeaweedFS, gateway S3) e `api`.

Uso: `cp .env.example .env`, preencher `POSTGRES_PASSWORD`, `S3_ACCESS_KEY` e `S3_SECRET_KEY`,
depois `docker compose up -d --wait`. Só a API é publicada no host (`API_PORT`, padrão 8000, em `127.0.0.1`; ver `API_BIND`).
Teste de ponta a ponta (exige Docker): `make compose-smoke`.

Atenção: a porta da API é publicada apenas em `127.0.0.1` por padrão (`${API_BIND:-127.0.0.1}:${API_PORT:-8000}:8000`).
Para torná-la acessível à rede, defina `API_BIND=0.0.0.0` (ou o IP de uma interface específica) no `.env`;
expor assim exige proteger a API (firewall ou proxy reverso). Use `API_PORT` para mudar a porta.
