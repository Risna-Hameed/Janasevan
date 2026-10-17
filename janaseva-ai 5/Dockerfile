# Deploy target: Render (or any Docker host). Owned by P3.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-mal \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
ENV PYTHONUNBUFFERED=1
# app.py (shared) must expose `app`. Render sets $PORT.
CMD gunicorn -w 2 -b 0.0.0.0:${PORT:-8000} app:app
