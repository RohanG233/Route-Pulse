# RoutePulse backend — production Docker image
#
# Multi-stage build:
#   Stage 1 (builder): install dependencies into a venv
#   Stage 2 (runtime): copy the venv and source; run as non-root

# ---------------------------------------------------------------------------
# Stage 1 — dependency builder
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# System packages needed for some compiled wheels (numpy etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Create isolated venv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install runtime dependencies only (no pytest/httpx in the image)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# ---------------------------------------------------------------------------
# Stage 2 — runtime image
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Non-root user for security
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
COPY app/ ./app/
COPY pytest.ini .

# The generated/ directory is created at runtime by the app's lifespan handler
# if it doesn't already exist — no need to COPY it here.
RUN mkdir -p app/data/generated && chown -R appuser:appuser /app

USER appuser

# Default env — override via docker run -e or docker-compose environment:
ENV HOST=0.0.0.0 \
    PORT=8000 \
    WORKERS=1 \
    LOG_LEVEL=info \
    CORS_ORIGINS=http://localhost:5173

EXPOSE 8000

# Generate the dataset if needed, then start the server.
# Use exec form so uvicorn receives SIGTERM cleanly.
CMD ["sh", "-c", \
     "python -m app.data.generate_dataset && \
      exec uvicorn app.main:app \
        --host $HOST \
        --port $PORT \
        --workers $WORKERS \
        --log-level $LOG_LEVEL"]
