# Daily extractor image: SMARD + Open-Meteo raw JSON into /data (energy_lakehouse.extract_local).
# Extraction only. Uploading to the landing volume stays in the workflow; no Databricks
# credentials go into the image or the container.
#
#   docker build -t energy-extractor:dev .
#   docker run --rm energy-extractor:dev --help
#   docker run --rm -v "${PWD}/data/landing:/data" energy-extractor:dev --source all --weeks 8

ARG PYTHON_IMAGE=python:3.12.16-slim-trixie

# Builder: build the wheel and export the locked base dependencies (no dev group, no ml extra).
FROM ghcr.io/astral-sh/uv:0.13.1 AS uv
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
# Only what the wheel build needs: metadata, lock, readme (project.readme), package, contracts
# (force-included as package data).
COPY pyproject.toml uv.lock README.md ./
COPY src/energy_lakehouse ./src/energy_lakehouse
COPY contracts ./contracts
RUN uv build --wheel --out-dir /dist \
    && uv export --frozen --no-dev --no-emit-project --format requirements-txt \
        -o /dist/requirements.txt

# Runtime: the wheel plus hash-checked base dependencies, nothing else.
FROM ${PYTHON_IMAGE}
ARG VERSION=0.1.0
ARG REVISION=unknown
# Repo URL; GHCR links the package to this repository (deploy-prod.yml passes it).
ARG SOURCE=https://github.com/tutul032/energy-lakehouse-de
LABEL org.opencontainers.image.title="energy-extractor" \
      org.opencontainers.image.description="SMARD + Open-Meteo raw extractor (energy_lakehouse)" \
      org.opencontainers.image.source="${SOURCE}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EXTRACT_OUT=/data

RUN --mount=type=bind,from=builder,source=/dist,target=/dist \
    pip install --require-hashes -r /dist/requirements.txt \
    && pip install --no-deps /dist/*.whl

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir /data \
    && chown app:app /data

WORKDIR /app
# Numeric uid:gid of "app", so runtimes can verify non-root (e.g. Kubernetes runAsNonRoot).
USER 10001:10001
VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "energy_lakehouse.extract_local"]
CMD ["--source", "all"]
