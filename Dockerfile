FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    git jq curl ca-certificates gh && \
    rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -f /etc/apt/sources.list.d/nodesource.list && \
    rm -f /etc/apt/keyrings/nodesource.gpg && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE /app/
COPY ralph_loop/ /app/ralph_loop/
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir "/app[visual]" && \
    rm -f /etc/apt/sources.list.d/nodesource* && \
    mkdir -p /ms-playwright && \
    python -m playwright install --with-deps chromium && \
    chmod -R a+rX /ms-playwright

RUN useradd -m -u 1000 ralph
USER ralph

WORKDIR /workspace
ENTRYPOINT ["ralph-loop"]

FROM base AS dev
USER root
RUN pip install --no-cache-dir pytest pytest-cov ruff mypy types-PyYAML
USER ralph
ENTRYPOINT []
CMD ["bash"]

FROM base AS codex
USER root
RUN npm install -g @openai/codex
USER ralph

FROM base AS copilot
USER root
RUN curl -fsSL https://gh.io/copilot-install | bash
USER ralph

FROM base AS claude
USER root
RUN npm install -g @anthropic-ai/claude-code
USER ralph
