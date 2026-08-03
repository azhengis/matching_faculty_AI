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

# Unpack the committed public-data seed and BUILD THE EMBEDDING INDEXES NOW,
# at image build time rather than at boot. On a host with no persistent disk
# (Render's free tier destroys the filesystem on every spin-down), an index
# built at boot is rebuilt on every wake — 9.5 minutes before the first
# response. Baked into the image, boot is just the model load.
#
# The seed carries public faculty data only; pipeline/check_seed_pii.py gates
# it. Real user data never enters an image — see DATA_DIR below.
RUN gunzip -c data/seed_faculty.db.gz > /app/faculty.db \
    && python -c "\
import search as sm; \
p = sm.load_faculty(); m = sm.load_model(); \
sm.get_index(p, m); sm.get_paper_index(p, m); \
print('indexes baked')" \
    && ls -la /app/faculty_index.pkl /app/paper_index.pkl

# Where writable state goes. Unset (the default) means /app — correct for a
# free host with an ephemeral filesystem, where accounts reset on restart and
# the baked seed is restored each time. Set DATA_DIR=/data on any host with a
# mounted volume and user data survives redeploys instead.
#   docker run -e DATA_DIR=/data -v faculty_data:/data ...
EXPOSE 8000

# Single worker on purpose. Measured steady state is ~280MB resident (SPECTER2's
# weights are mmap'd from safetensors, so they cost far less than their 440MB
# on-disk size). Each additional worker loads its own copy, so a second one
# roughly doubles that for a tool with a handful of concurrent users.
# Bind to $PORT when the platform sets one (Render and most PaaS hosts inject
# it and health-check that exact port), falling back to 8000 locally. Hardcoding
# 8000 passes locally and then fails the health check on deploy, which is a
# miserable thing to debug through a 15-minute build loop.
CMD ["sh", "-c", "uvicorn web_app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
