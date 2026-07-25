FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY app ./app
COPY web ./web
COPY data ./data

EXPOSE 8000

# Render/Railway inject $PORT; default to 8000 for a plain `docker run`.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
