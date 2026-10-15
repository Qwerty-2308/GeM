# GeM Assistant as a website. Build from the repository root:
#   docker build -t gem-assistant .
# Run it with a disk that persists at /data and the secrets as environment variables
# (never baked into the image):
#   docker run -p 8080:8080 -v gem-data:/data \
#     -e GEM_PASSWORD=... -e GROQ_API_KEY=... -e GEM_CONTACT_EMAIL=... gem-assistant
# Run exactly one copy: the data is one SQLite file and GeM's request limits are kept in it.
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    GEM_DATA_DIR=/data \
    GEM_FRONTEND_DIR=/app/frontend

WORKDIR /app/backend
# Dependencies first, exactly as uv.lock pins them, so a code change doesn't reinstall them.
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --locked --no-dev --no-install-project
# Headless Chromium for GeM's public bid list, with the system libraries it needs.
RUN uv run --no-sync playwright install --with-deps chromium
COPY backend/ ./
COPY frontend/ /app/frontend/
RUN uv sync --locked --no-dev

VOLUME /data
EXPOSE 8080
# Hosts that set PORT (Render, Railway, ...) get it; otherwise 8080.
CMD ["sh", "-c", "exec /app/.venv/bin/gem-assistant serve --host 0.0.0.0 --port ${PORT:-8080} --no-open"]
