FROM python:3.9-slim

# Install FFmpeg
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

# Expose the port
EXPOSE 8080

# Command to run the application with increased timeout and worker configuration
CMD ["gunicorn", "app:app", \
     "--bind", "0.0.0.0:8080", \
     "--timeout", "300", \
     "--workers", "2", \
     "--threads", "2", \
     "--worker-class", "gthread"]
