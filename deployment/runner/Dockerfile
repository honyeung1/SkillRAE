FROM python:3.12-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ARG HARBOR_VERSION=0.1.45
ARG CODEX_VERSION=0.116.0
ARG DOCKER_COMPOSE_VERSION=2.40.3
ARG PYTORCH_CPU_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu

RUN apt-get update && apt-get install -y \
    bash \
    ca-certificates \
    curl \
    docker.io \
    git \
    nodejs \
    npm \
    tini \
    && rm -rf /var/lib/apt/lists/*

RUN arch="$(dpkg --print-architecture)" \
    && case "$arch" in \
      amd64) compose_arch="x86_64" ;; \
      arm64) compose_arch="aarch64" ;; \
      *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac \
    && mkdir -p /usr/libexec/docker/cli-plugins \
    && curl -fsSL "https://github.com/docker/compose/releases/download/v${DOCKER_COMPOSE_VERSION}/docker-compose-linux-${compose_arch}" \
      -o /usr/libexec/docker/cli-plugins/docker-compose \
    && chmod +x /usr/libexec/docker/cli-plugins/docker-compose

RUN python -m venv /opt/skillsbench-runner
ENV PATH="/opt/skillsbench-runner/bin:/root/.local/bin:${PATH}"

COPY deployment/runner/requirements-harbor312.txt /tmp/requirements-harbor312.txt
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir --extra-index-url "${PYTORCH_CPU_EXTRA_INDEX_URL}" -r /tmp/requirements-harbor312.txt \
    && rm -f /tmp/requirements-harbor312.txt

RUN npm install -g "@openai/codex@${CODEX_VERSION}"
RUN uv tool install "harbor==${HARBOR_VERSION}"

RUN mkdir -p /workspace/skillsbench-private /workspace/artifacts

COPY deployment/runner/entrypoint.sh /usr/local/bin/skillsbench-runner-entrypoint
RUN chmod +x /usr/local/bin/skillsbench-runner-entrypoint

WORKDIR /workspace/skillsbench-private

ENTRYPOINT ["tini", "--", "/usr/local/bin/skillsbench-runner-entrypoint"]
CMD ["bash"]
