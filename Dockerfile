FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /workspace
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=180 --retries=5 -r requirements.txt \
    && groupadd --system app && useradd --system --gid app --home-dir /workspace app \
    && mkdir -p /workspace/data/artifacts && chown -R app:app /workspace
COPY --chown=app:app . .
USER app
EXPOSE 8000 8501
