FROM python:3.14-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && gosu nobody true

WORKDIR /app
COPY pyproject.toml ./
COPY scenehound/ ./scenehound/
RUN pip install --no-cache-dir .

RUN mkdir -p /config
VOLUME /config
ENV SCENEHOUND_CONFIG_DIR=/config
ENV PUID=99
ENV PGID=100
EXPOSE 9797

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Runs as root only to fix /config ownership; the entrypoint drops to PUID:PGID via gosu.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9797/healthz', timeout=3).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "--factory", "scenehound.app:create_app", "--host", "0.0.0.0", "--port", "9797"]
