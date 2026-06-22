FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/leogallego/ansible-know-mcp" \
      org.opencontainers.image.description="Ansible Know MCP Server — module and role discovery, documentation, and skill generation" \
      org.opencontainers.image.license="GPL-3.0-or-later"

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH="/home/user/.local/bin:$PATH"

WORKDIR /home/user/app

RUN pip install --no-cache-dir --user ansible-core

COPY --chown=user:user pyproject.toml README.md LICENSE ./
COPY --chown=user:user src/ src/
RUN pip install --no-cache-dir --user .

EXPOSE 7860

ENV ANSIBLE_KNOW_TRANSPORT=http \
    ANSIBLE_KNOW_PORT=7860 \
    ANSIBLE_KNOW_HOST=0.0.0.0 \
    ANSIBLE_KNOW_SKIP_UPDATE_CHECK=1

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import socket; s = socket.create_connection(('localhost', 7860), timeout=5); s.close()"

CMD ["ansible-know-mcp"]
