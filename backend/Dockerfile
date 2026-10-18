# Multi-agent research assistant API. Set ANTHROPIC_API_KEY (without it, tasks answer 503).
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Tests and lint: docker build --target test -t agents-test . && docker run --rm agents-test
FROM base AS test
COPY requirements-dev.txt pyproject.toml ./
RUN pip install -r requirements-dev.txt
COPY main.py ./
COPY app ./app
COPY tests ./tests
CMD ["sh", "-c", "ruff check . && ruff format --check . && pytest"]

FROM base AS runtime
COPY main.py ./
COPY app ./app
# The memory database lives in DATA_DIR: mount a volume there to keep it across deploys.
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
USER app
ENV PORT=8000 DATA_DIR=/data
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
