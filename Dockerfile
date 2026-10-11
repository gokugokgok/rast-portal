FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SEARXNG_SETTINGS_PATH=/app/searxng-settings.yml \
    TMPDIR=/tmp/portal-cache \
    PYTHONPATH=/opt/searxng

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates && rm -rf /var/lib/apt/lists/*

# Pin the tested upstream revision.
RUN git init /opt/searxng && cd /opt/searxng \
    && git remote add origin https://github.com/searxng/searxng.git \
    && git fetch --depth 1 origin 6a0ac6a39112d4a73a80eda4564d68ba090a9eff \
    && git checkout --detach FETCH_HEAD

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r /opt/searxng/requirements.txt -r requirements.txt
COPY . /app
RUN cd /opt && tar --exclude=.git --exclude=__pycache__ -czf /opt/searxng-source.tar.gz searxng \
    && mkdir /opt/source-bundle \
    && cp /opt/searxng-source.tar.gz /opt/source-bundle/ \
    && cp -r /app /opt/source-bundle/portal \
    && cp /opt/searxng/LICENSE /opt/source-bundle/SEARXNG-LICENSE \
    && tar -czf /opt/portal-source.tar.gz -C /opt/source-bundle . \
    && rm -rf /opt/source-bundle /opt/searxng-source.tar.gz \
    && useradd --create-home --uid 10001 portal \
    && mkdir -p /tmp/portal-cache && chown portal:portal /tmp/portal-cache

USER portal
EXPOSE 10000
CMD ["python", "start.py"]
