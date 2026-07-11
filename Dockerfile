FROM python:3.12-slim

RUN groupadd -g 1000 scenehound && useradd -u 1000 -g scenehound -m scenehound

WORKDIR /app
COPY pyproject.toml ./
COPY scenehound/ ./scenehound/
RUN pip install --no-cache-dir .

RUN mkdir -p /config && chown scenehound:scenehound /config
VOLUME /config
ENV SCENEHOUND_CONFIG_DIR=/config
EXPOSE 9797

USER scenehound
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9797/healthz', timeout=3).status==200 else 1)"

CMD ["uvicorn", "--factory", "scenehound.app:create_app", "--host", "0.0.0.0", "--port", "9797"]
