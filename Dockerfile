# Direct to Vet - Production Dockerfile
# Optimized for AWS AppRunner and similar container platforms
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (including FFmpeg for audio transcription, curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser

# Copy requirements first (for layer caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Create data directory and set ownership
RUN mkdir -p /app/data && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port (AppRunner uses 8080)
EXPOSE 8080

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV ENV=production
ENV PORT=8080

# Health check (AppRunner checks /health endpoint)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
