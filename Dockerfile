FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY config ./config
RUN mkdir -p /data && useradd --system --uid 10001 app && chown -R app /data
USER app
CMD ["python", "-m", "app.main"]

