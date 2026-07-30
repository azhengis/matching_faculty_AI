# syntax=docker/dockerfile:1
FROM python:3.11-slim

# libgomp1 is required by torch; the rest of the build-essential chain is not,
# so we stay on slim rather than the full image (~700MB saved).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch first, and pinned to the CPU wheel index. The default PyPI
# wheel bundles CUDA and is ~2.5GB — on a host with no GPU that is pure weight,
# and it pushes the image past most free-tier build limits.
RUN pip install --no-cache-dir torch==2.13.0 \
        --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
# torch is already installed above; drop it from the file so pip doesn't pull
# the CUDA wheel over the top of the CPU one.
RUN grep -v '^torch==' requirements.txt > /tmp/req.txt \
    && pip install --no-cache-dir -r /tmp/req.txt

# Bake the embedding model into the image. Downloading it at boot costs ~90s of
# cold start on every restart and makes the app depend on HuggingFace being up.
ENV HF_HOME=/app/.hf_cache
RUN python -c "\
from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('allenai/specter2_base'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-12-v2', max_length=512)"

COPY . .

# faculty.db and the .pkl indexes live on a mounted volume, not in the image:
# the database holds user accounts and proposals and must survive a redeploy.
ENV DB_PATH=/data/faculty.db
EXPOSE 8000

# Single worker on purpose. Measured steady state is ~280MB resident (SPECTER2's
# weights are mmap'd from safetensors, so they cost far less than their 440MB
# on-disk size). Each additional worker loads its own copy, so a second one
# roughly doubles that for a tool with a handful of concurrent users.
CMD ["uvicorn", "web_app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
