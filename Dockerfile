# syntax=docker/dockerfile:1

# ---------- Étape 1 : construction des dépendances ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# ---------- Étape 2 : image finale ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DATA_DIR=/data

COPY --from=builder /opt/venv /opt/venv

# Utilisateur non privilégié
RUN useradd --create-home --uid 1000 larbinus \
 && mkdir -p /data /documents \
 && chown -R larbinus:larbinus /data /documents

WORKDIR /app
COPY --chown=larbinus:larbinus app/ ./app/

USER larbinus
EXPOSE 8080
VOLUME ["/data", "/documents"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
