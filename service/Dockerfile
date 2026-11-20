# Build context is the repository root (the service imports the existing pdf2slides package).
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY service/pyproject.toml service/requirements.lock ./service/
COPY service/src ./service/src
RUN pip install --no-cache-dir -c service/requirements.lock . ./service

COPY service/alembic.ini ./service/
COPY service/alembic ./service/alembic

RUN useradd --create-home --uid 10001 app
USER app
WORKDIR /app/service
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn p2s_service.api.app:build_default_app --factory --host 0.0.0.0 --port ${PORT:-8080}"]
