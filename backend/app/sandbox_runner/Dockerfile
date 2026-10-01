# Operator builds this trusted image ahead of time, never from a repository Dockerfile.
FROM node:24-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pytest git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
USER 65532:65532
WORKDIR /work
# No VOLUME, secrets, package installations or downloads during execution.
