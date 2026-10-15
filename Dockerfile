FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY src/pyproject.toml src/pyproject.toml
RUN pip install --no-cache-dir uv && uv pip install --system --no-cache -r src/pyproject.toml

# Copy application code
COPY src/ src/
COPY protocol/ protocol/

COPY frontend/dist/ frontend/dist/
COPY config.example.yaml VERSION install.sh ./

# Create directories for runtime
RUN mkdir -p /root/.tokenage/logs

# Copy entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Copy supervisord config
COPY docker/supervisord.conf /etc/supervisor/supervisord.conf

EXPOSE 4001

ENV TOKENAGE_CONFIG=/root/.tokenage/config.yaml

ENTRYPOINT ["/entrypoint.sh"]
