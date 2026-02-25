FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml .
COPY src/ src/

RUN uv pip install --system .

# Modal auth is passed via environment variables:
#   MODAL_TOKEN_ID and MODAL_TOKEN_SECRET
# Set these when running the container.

ENTRYPOINT ["modal-mcp-server"]
