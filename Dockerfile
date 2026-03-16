# ============================================================
# Dockerfile for FinResearchAgent
# Builds a container that runs on Google Cloud Run
# ============================================================

# Use the official Python 3.11 slim image
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies (ca-certificates for HTTPS requests)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates pandoc && \
    rm -rf /var/lib/apt/lists/*

# Copy dependency file first (allows Docker to cache this layer)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the entire project into the container
COPY . .

# Create a non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port 8080 (Cloud Run default)
EXPOSE 8080

# Set environment variables for Cloud Run
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Health check (Cloud Run uses this to verify the container is ready)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

# Start the FastAPI server
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1
