import multiprocessing

# Gunicorn configuration for Railway deployment

# Worker settings
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = 'sync'  # Using sync workers for file processing
timeout = 300  # 5 minutes timeout
keepalive = 65

# Server settings
bind = "0.0.0.0:$PORT"  # Railway provides PORT environment variable
worker_tmp_dir = "/dev/shm"  # Use shared memory for temporary files
preload_app = True

# Logging
accesslog = "-"  # Log to stdout
errorlog = "-"   # Log to stderr
loglevel = "info"

# SSL/TLS settings (if needed)
# keyfile = 'ssl/key.pem'
# certfile = 'ssl/cert.pem'
