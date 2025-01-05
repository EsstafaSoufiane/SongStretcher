FROM python:3.9-slim

# Install FFmpeg and cleanup
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy the rest of the application
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV WORKERS=1
ENV THREADS=4
ENV TIMEOUT=600
ENV MAX_REQUESTS=1000
ENV WORKER_CLASS=sync

# Create directory for temporary files
RUN mkdir -p /app/temp

# Expose the port
EXPOSE 8080

# Command to run the application
CMD gunicorn app:app \
    --bind 0.0.0.0:8080 \
    --workers ${WORKERS} \
    --threads ${THREADS} \
    --timeout ${TIMEOUT} \
    --max-requests ${MAX_REQUESTS} \
    --worker-class ${WORKER_CLASS} \
    --log-level info
