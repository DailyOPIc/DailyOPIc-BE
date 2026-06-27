FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    QUESTION_PATTERNS_PATH=/app/data/question_patterns.json

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Build from the workspace root:
# docker build -f DailyOPIc-BE/Dockerfile -t dailyopic-api .
COPY DailyOPIc-BE/pyproject.toml /app/pyproject.toml
COPY DailyOPIc-BE/app /app/app

RUN pip install --no-cache-dir .

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
