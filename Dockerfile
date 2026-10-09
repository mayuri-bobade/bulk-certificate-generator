FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching. psycopg is included so
# the same image can talk to PostgreSQL (see docker-compose.yml).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]>=3.1"

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY pytest.ini ./pytest.ini
COPY tests ./tests

RUN useradd --create-home appuser \
    && mkdir -p /data /app/storage \
    && chown -R appuser:appuser /app /data
USER appuser

ENV DATABASE_URL=sqlite:////data/certificates.db \
    STORAGE_DIR=/app/storage

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
