FROM python:3.12-slim

# libgl1 + libglib2.0-0 required by OpenCV
# libgomp1 required by PyTorch / YOLO operations
# ffmpeg is the RTSP fallback: opencv-python bundles a frozen FFmpeg 4.4 that
# cannot decode a good number of cameras (see vehicles/ffmpeg_capture.py), and
# without a system ffmpeg those cameras have no working backend at all.
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# PaddlePaddle CPU build. Paddle 3.x is required for numpy 2 compatibility;
# the old paddlepaddle-gpu==2.6.2.post120 pin is not on PyPI and breaks numpy 2.
RUN pip install --no-cache-dir paddlepaddle==3.0.0

# NOTE: this Dockerfile builds from the REPOSITORY ROOT, not ./backend — see
# docker-compose.yml. The canonical dependency list lives at the root, and
# backend/requirements.txt only redirects to it with a relative `-r ../`, which
# cannot resolve from a ./backend build context.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy entrypoint outside /app so the volume mount does not override it
COPY backend/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY backend/ .

ENTRYPOINT ["/entrypoint.sh"]

# Default command (backend service). Celery service overrides this in docker-compose.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
