# Dependências transitivas Python (licenças)

Arquivo GERADO por `make licencas-sync` (verificador de licenças, F0-04). Não edite à mão sem
conferir os metadados do pacote. Registra só nome, licença normalizada, grupo e quem exige;
sem versão, de propósito (ver ADR-004). Linhas a mais são permitidas (a árvore varia com a
versão do Python); linha faltando, licença divergente ou grupo `runtime` registrado como `dev`
falham o `make check`. O modo de verificação nunca escreve neste arquivo.

| Nome | Licença (SPDX) | Grupo | Exigido por |
|------|----------------|-------|-------------|
| annotated-doc | MIT | runtime | fastapi |
| annotated-types | MIT | runtime | pydantic |
| anyio | MIT | runtime | starlette |
| ast-serialize | MIT | dev | mypy |
| botocore | Apache-2.0 | runtime | boto3, s3transfer |
| certifi | MPL-2.0 | dev | httpcore, httpx |
| click | BSD-3-Clause | runtime | uvicorn |
| h11 | MIT | runtime | uvicorn |
| httpcore | BSD-3-Clause | dev | httpx |
| idna | BSD-3-Clause | runtime | anyio |
| iniconfig | MIT | dev | pytest |
| jmespath | MIT | runtime | boto3, botocore |
| librt | MIT | dev | mypy |
| mypy-extensions | MIT | dev | mypy |
| opentelemetry-api | Apache-2.0 | runtime | fastapi |
| pathspec | MPL-2.0 | dev | mypy |
| pluggy | MIT | dev | pytest |
| pydantic | MIT | runtime | fastapi |
| pydantic-core | MIT | runtime | pydantic |
| pygments | BSD-2-Clause | dev | pytest |
| python-dateutil | Apache-2.0 OR BSD-3-Clause | runtime | botocore |
| s3transfer | Apache-2.0 | runtime | boto3 |
| six | MIT | runtime | python-dateutil |
| starlette | BSD-3-Clause | runtime | fastapi |
| typing-extensions | PSF-2.0 | runtime | anyio, fastapi, opentelemetry-api, pydantic, pydantic-core, starlette, typing-inspection |
| typing-inspection | MIT | runtime | fastapi, pydantic |
| urllib3 | MIT | runtime | botocore |
