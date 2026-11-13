FROM python:3.13.13-slim-bookworm@sha256:355bfa66770995d7e9a0da4b3473b44d0cb451f6b56f5615ad9c39e3c4eca03f AS base
COPY requirements.system.lock /tmp/system.lock
RUN apt-get update -qq \
    && xargs --no-run-if-empty env DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends --only-upgrade < /tmp/system.lock \
    && rm -rf /var/lib/apt/lists/* /tmp/system.lock

FROM base AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY requirements.lock requirements.build.lock ./
ARG SOURCE_DATE_EPOCH=315532800
ENV SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH
RUN pip install --no-cache-dir --no-compile --require-hashes -r requirements.build.lock \
    && pip install --no-cache-dir --no-compile --require-hashes --prefix=/install -r requirements.lock
COPY src ./src
RUN pip install --no-cache-dir --no-compile --no-deps --no-build-isolation --prefix=/install .

FROM base
LABEL org.opencontainers.image.title="Swarmtenderr" \
      org.opencontainers.image.description="An explainable qBittorrent dashboard and queue manager" \
      org.opencontainers.image.licenses="MIT"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=builder /install /usr/local
RUN groupadd --gid 10001 swarmtenderr && useradd --uid 10001 --gid 10001 --no-create-home swarmtenderr \
    && mkdir /data && chown 10001:10001 /data
USER 10001:10001
VOLUME /data
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3).close()"]
ENTRYPOINT ["apollo-swarmtenderr"]
