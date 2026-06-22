FROM python:3.12-slim

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
    CMD python -c "import httpx; httpx.get('http://localhost:7860/mcp').raise_for_status()" || exit 1

CMD ["ansible-know-mcp"]
