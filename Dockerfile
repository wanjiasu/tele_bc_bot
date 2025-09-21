# Use official Python runtime as a parent image
FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# System deps (certificate + build tools if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (leverage layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY app.py ./

# Create a non-root user for security
RUN useradd -u 10001 -m appuser && chown -R appuser:appuser /app
USER appuser

# Expose the port Flask runs on
EXPOSE 5000

# Default envs (can be overridden by compose)
ENV FLASK_ENV=production \
    DB_PATH=/data/bot.db

# Create volume mount point for SQLite database
VOLUME ["/data"]

# Use gunicorn for production serving
# Note: we bind to 0.0.0.0:5000 to accept external traffic
CMD ["sh", "-lc", "gunicorn -w ${WEB_CONCURRENCY:-2} -k gthread --threads ${WEB_THREADS:-4} -b 0.0.0.0:5000 app:app"]