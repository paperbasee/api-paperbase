FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    awscli \
    bash \
    gzip \
    libpq5 \
    postgresql-client \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app/

RUN chmod +x /app/entrypoint.sh \
    && adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["./entrypoint.sh"]
