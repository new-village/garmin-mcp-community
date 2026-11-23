FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install .

# Cloud Run injects PORT; server.py respects it (default 8080).
EXPOSE 8080

CMD ["garmin-mcp-community"]
