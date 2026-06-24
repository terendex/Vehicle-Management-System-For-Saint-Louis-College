FROM python:3.12-slim

# libgl1 + libglib2.0-0 required by OpenCV
# libgomp1 required by PyTorch / YOLO operations
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy entrypoint outside /app so the volume mount does not override it
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY . .

ENTRYPOINT ["/entrypoint.sh"]

# Default command (backend service). Celery service overrides this in docker-compose.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
