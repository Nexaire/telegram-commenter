FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 app \
    && mkdir -p /data \
    && chown -R app:app /data
COPY app ./app
COPY config ./config
USER app
CMD ["python", "-m", "app.main"]
