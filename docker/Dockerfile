# ─────────────────────────────────────────────────────────────────────────────
#  Railway image: React bundle + Django/Daphne + the full ML stack, one service.
#
#  Built from the REPOSITORY ROOT (not ./backend) because stage 1 needs the
#  frontend sources. backend/Dockerfile is left alone — docker-compose still
#  uses it for local development.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: build the React bundle ──────────────────────────────────────────
# Node 22: Vite 8 requires >=20.19, and node:22 is the current LTS line.
FROM node:22-slim AS frontend

WORKDIR /build

# Copy manifests first so `npm ci` is cached and only re-runs when deps change.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ── Stage 2: Python dependencies ─────────────────────────────────────────────
FROM python:3.12-slim AS deps

# libgl1 + libglib2.0-0 for OpenCV, libgomp1 for the PyTorch/YOLO kernels.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Build the venv separately so the final stage copies it in one layer.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip

# CPU-only torch. PyPI's default linux wheel bundles ~2.5GB of CUDA libraries
# (nvidia-cudnn, nvidia-cublas, ...) that a Railway container can never use —
# there is no GPU attached. The CPU wheel runs the identical models at ~200MB.
# Install it BEFORE requirements.txt so ultralytics finds torch already
# satisfied and does not pull the CUDA build back in as a dependency.
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.12.0 torchvision==0.27.0

# PaddlePaddle CPU build. Paddle 3.x is required for numpy 2 compatibility.
RUN pip install --no-cache-dir paddlepaddle==3.0.0

# The canonical list is the root one; backend/requirements.txt only redirects to
# it, and that relative -r would not resolve from this stage's working directory.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ── Stage 3: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deps /opt/venv /opt/venv

# Each ENV is its own instruction: a comment inside a line-continuation is not
# reliably stripped by every builder and can break the parse.
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Ultralytics writes a settings file and checks for updates on import. Both are
# pointless in an immutable container, and the update check puts a network
# round-trip in front of the first inference.
ENV YOLO_CONFIG_DIR=/tmp/ultralytics
ENV MPLCONFIGDIR=/tmp/matplotlib

# Cap the intra-op thread pool. Torch defaults to one thread per core; on a
# small shared container that oversubscribes the CPU and makes inference slower,
# not faster.
ENV OMP_NUM_THREADS=2

WORKDIR /app

COPY backend/ /app/

# The trained detectors. .dockerignore excludes *.pt wholesale (weights are
# volume-mounted in docker-compose), so the two deployable models are copied
# back in explicitly — without them detection.py falls back and OCR degrades.
COPY backend/scanning/ml/runs/plate_detector/weights/best.pt   /app/scanning/ml/runs/plate_detector/weights/best.pt
COPY backend/scanning/ml/runs/vehicle_detector/weights/best.pt /app/scanning/ml/runs/vehicle_detector/weights/best.pt

# The built SPA. settings.FRONTEND_BUILD_DIR points here; its existence is what
# switches on WhiteNoise's web-root serving and the SPA catch-all route.
COPY --from=frontend /build/dist /app/frontend_build

COPY backend/start.sh /start.sh
RUN chmod +x /start.sh

# Collect Django's own admin/DRF static files at build time so no deploy pays
# for it and the container filesystem can stay read-only.
RUN DEBUG=false SECRET_KEY=build-only-not-a-real-secret \
    python manage.py collectstatic --noinput --clear

# Pre-compile bytecode for the app and its dependencies. Without this, every
# container start re-parses the whole torch/paddle/ultralytics source tree
# before serving a single request, which dominates cold-start time. Failures
# here are non-fatal: a module that will not pre-compile still imports normally.
RUN python -m compileall -q /opt/venv/lib /app || true

CMD ["/start.sh"]
