# Node base (the Deepline CLI is npm-distributed; stages 3 and 5 shell out to it)
# plus Debian's Python for the pipeline itself. Avoids piping a setup script to bash.
FROM node:20-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEEPLINE_DISABLE_AUTO_UPDATE=1 \
    DATA_DIR=/data/data \
    OUT_DIR=/data/out \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv "$VIRTUAL_ENV" \
 && npm install -g deepline@latest \
 && npm cache clean --force

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ ./scripts/
COPY config/ ./config/
COPY app/ ./app/
COPY seed/ ./seed/

# the Railway volume mounts at /data; create the tree in case it is empty
RUN mkdir -p /data/data /data/out

EXPOSE 8000
CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
