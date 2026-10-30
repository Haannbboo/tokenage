FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS dashboard
WORKDIR /build/frontend
COPY frontend/package.json ./
RUN npm install --no-package-lock
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm AS server
COPY --from=ghcr.io/astral-sh/uv:0.11.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY src/pyproject.toml src/pyproject.toml
RUN uv pip install --system --no-cache -r src/pyproject.toml
COPY src/ src/
COPY protocol/ protocol/
COPY install.sh VERSION config.example.yaml ./
COPY --from=dashboard /build/frontend/dist/ frontend/dist/
COPY docker/entrypoint.sh /entrypoint.sh
RUN groupadd --gid 10001 tokenage && useradd --uid 10001 --gid tokenage --create-home tokenage \
    && mkdir /data && chown tokenage:tokenage /data \
    && chmod 755 /entrypoint.sh && chmod -R a+rX /app
ARG GIT_COMMIT=unknown
ENV TOKENAGE_HOME=/data TOKENAGE_GIT_COMMIT=$GIT_COMMIT PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENV TOKENAGE_API_HOST=0.0.0.0 TOKENAGE_API_PORT=4001
LABEL org.opencontainers.image.source="https://github.com/haannbboo/tokenage" \
      org.opencontainers.image.revision=$GIT_COMMIT
USER tokenage
EXPOSE 4001
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4001/version', timeout=4).read()"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "src.api:app", "-c", "src/config/api.conf.py", "--access-logfile", "-", "--error-logfile", "-"]
