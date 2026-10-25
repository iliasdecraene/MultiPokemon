FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YL_DATA_DIR=/data \
    YL_CONFIG=/data/config.json

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY yellowlink_server ./yellowlink_server

# Unprivileged user (uid 1000). The data dir must be writable by this uid.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin yellowlink \
    && mkdir -p /data && chown yellowlink:yellowlink /data
USER yellowlink

VOLUME ["/data"]
EXPOSE 8765

# Settings come from env (YL_KEY, YL_PORT) and, if present, /data/config.json.
CMD ["python", "-m", "yellowlink_server", "--host", "0.0.0.0"]
