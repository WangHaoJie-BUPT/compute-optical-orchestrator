FROM node:24-bookworm-slim AS console-builder

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./

RUN npm ci

COPY frontend/ ./

RUN npm test \
    && npm run build

FROM python:3.10-slim-bookworm

ARG KUBECTL_VERSION=v1.35.5
ARG TARGETARCH=amd64

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y \
      --no-install-recommends \
      ca-certificates \
      curl \
    && curl -fsSLo /tmp/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
    && curl -fsSLo /tmp/kubectl.sha256 \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl.sha256" \
    && echo "$(cat /tmp/kubectl.sha256)  /tmp/kubectl" \
      | sha256sum --check \
    && install \
      -o root \
      -g root \
      -m 0755 \
      /tmp/kubectl \
      /usr/local/bin/kubectl \
    && rm -f \
      /tmp/kubectl \
      /tmp/kubectl.sha256 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-runtime.txt /app/requirements-runtime.txt

RUN python -m pip install --upgrade \
      pip \
      setuptools \
      wheel \
    && python -m pip install \
      -r /app/requirements-runtime.txt

COPY third_party/oopt-gnpy /app/third_party/oopt-gnpy

RUN PBR_VERSION=2.14.1.dev5 python -m pip install \
      --editable \
      /app/third_party/oopt-gnpy \
    && python -c "import gnpy.yang.precision_dict"

COPY . /app

COPY --from=console-builder \
  /frontend/dist \
  /app/frontend/dist

RUN chmod +x /app/scripts/container_entrypoint.sh \
    && mkdir -p /data \
    && python -m compileall \
      /app/orchestrator \
      /app/scripts

EXPOSE 8000
EXPOSE 9100

ENTRYPOINT ["/app/scripts/container_entrypoint.sh"]

CMD ["python", "scripts/run_runtime_api.py", "--database", "/data/runtime-orchestrator.db", "--host", "0.0.0.0", "--port", "8000", "--metrics-host", "0.0.0.0", "--metrics-port", "9100"]
