# syntax=docker/dockerfile:1
#
# Two stages, and the split is the whole point: torch is needed to EXPORT the
# model and BUILD the indexes, but not to serve. Keeping it out of the runtime
# image is what makes this fit a 512MB instance.
#
#   with torch    ~834 MB resident  -> OOMs Render free (observed)
#   without torch ~340 MB resident  -> fits with headroom
#
# 3.13 throughout: numpy 2.5.1 declares requires-python >=3.12 and ships no
# cp311 wheel, and 3.13 matches the venv the pins were generated from.
#
# ---------------------------------------------------------------------------
# Stage 1 — builder. Exports SPECTER2 to int8 ONNX and builds the embedding
# indexes. Everything heavy lives and dies here.
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
ENV HF_HOME=/build/.hf

# CPU torch only. The default PyPI wheel bundles CUDA (~2.5GB) that will never
# run on this host.
RUN pip install --no-cache-dir torch==2.13.0 \
        --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt requirements-build.txt ./
# The filtered copy MUST land beside requirements.txt: requirements-build.txt
# begins with `-r requirements.txt`, and pip resolves that relative to the
# including file's own directory. Writing it to /tmp made pip look for
# /tmp/requirements.txt and fail the build.
RUN grep -v '^torch==' requirements-build.txt > ./build-filtered.txt \
    && pip install --no-cache-dir -r ./build-filtered.txt \
    && rm ./build-filtered.txt

# NO `COPY . .` IN THIS STAGE — that is the caching. Each expensive step
# copies exactly the files it reads, so its layer is reused unless those
# specific files change. With the whole repo copied first, every commit —
# including a one-line prompt tweak — invalidated everything below and paid
# the full model export and index build again: ~30 minutes per deploy while
# iterating on wording. With targeted copies, those commits rebuild only the
# cheap runtime stage and deploy in a few minutes.

# Export + quantize: 437MB fp32 -> 110MB int8. Self-contained script (no
# project imports — verified by AST scan; keep it that way or this layer's
# file set below must grow with it). Deletes the fp32 graph afterwards so it
# cannot reach the runtime image by accident. Reruns only when the script
# itself (or a layer above) changes.
COPY pipeline/13_export_onnx.py pipeline/
RUN python pipeline/13_export_onnx.py --out /build/onnx_model

# Build the indexes with the fp32 reference model, NOT the quantized one.
# Measured: serving int8 queries against an fp32-built index gives 92% top-5
# overlap and 10/10 top-1 agreement against the fp32 reference, versus 88%
# when the index is rebuilt in int8 — quantizing one side beats quantizing
# both. Needs only search.py and the seed: search.py's onnx_encoder import is
# lazy inside load_model(), which this step never calls (verified by running
# exactly this in a directory containing only these two files). Reruns only
# when search.py or the seed data changes — which is when it SHOULD.
COPY search.py ./
COPY data/seed_faculty.db.gz data/
RUN gunzip -c data/seed_faculty.db.gz > /build/faculty.db \
    && python -c "\
import search as sm; \
sm.DB='/build/faculty.db'; sm.INDEX='/build/faculty_index.pkl'; sm.PAPER_INDEX='/build/paper_index.pkl'; \
from sentence_transformers import SentenceTransformer; \
m = SentenceTransformer('allenai/specter2_base'); \
p = sm.load_faculty(); sm.get_index(p, m); sm.get_paper_index(p, m); \
print('indexes built')" \
    && ls -la /build/faculty_index.pkl /build/paper_index.pkl

# ---------------------------------------------------------------------------
# Stage 2 — runtime. No torch, no transformers, no sentence-transformers.
# ---------------------------------------------------------------------------
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Artifacts from the builder: the quantized model, the prebuilt indexes, and
# the seeded database. Copied rather than rebuilt, so a cold start is just a
# model load — which matters on a free instance that wipes its disk on every
# spin-down and would otherwise rebuild on each wake.
COPY --from=builder /build/onnx_model        /app/onnx_model
COPY --from=builder /build/faculty_index.pkl /app/faculty_index.pkl
COPY --from=builder /build/paper_index.pkl   /app/paper_index.pkl
COPY --from=builder /build/faculty.db        /app/faculty.db

# onnxruntime otherwise grabs every visible core and thrashes on a shared CPU.
ENV ONNX_THREADS=2

# Guard against a silently broken image: a missing model would let the app boot
# and match nothing, which is far worse than failing the build here.
RUN python -c "\
import onnx_encoder; \
assert onnx_encoder.is_available(), 'ONNX model missing from runtime image'; \
v = onnx_encoder.encode(['a smoke test query']); \
assert v.shape == (1, 768), v.shape; \
print('onnx encoder OK', v.shape)"

# Where writable state goes. Unset (the default) means /app — correct for a
# free instance with an ephemeral filesystem, where accounts reset on restart
# and the baked seed is restored each time. Set DATA_DIR=/data on a host with a
# mounted volume and user data survives redeploys instead.
EXPOSE 8000

# Single worker on purpose: each loads its own copy of the model and indexes.
# Bind $PORT when the platform sets one — Render health-checks that exact port,
# and hardcoding 8000 passes locally then fails on deploy.
CMD ["sh", "-c", "uvicorn web_app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
