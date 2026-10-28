FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Instala somente dependências de runtime (sem extras de desenvolvimento).
COPY pyproject.toml README.md ./
COPY backend/src ./backend/src
RUN pip install .

RUN useradd --system --uid 10001 --no-create-home inovalytics
USER 10001

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

CMD ["uvicorn", "--factory", "inovalytics.api.main:app_factory", "--host", "0.0.0.0", "--port", "8000"]
