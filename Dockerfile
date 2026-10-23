# ==============================================================================
# NEXUS KEYS — Enterprise Production Dockerfile
# Optimized multi-stage, secure container for Python 3.11 / FastAPI
# ==============================================================================

FROM python:3.11-slim

# Set environment flags
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

# Install system dependencies for Pillow, ReportLab vector PDFs, and health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code and assets
COPY app/ ./app/
COPY static/ ./static/
COPY run.py .
COPY backup.py .
COPY .env.example .

# Create persistent data directories
RUN mkdir -p /app/data/backups /app/data/invoices /app/data/sent_receipts

# Expose HTTP port
EXPOSE 8000

# Health check to ensure service availability
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# Launch production server
CMD ["python", "run.py"]
