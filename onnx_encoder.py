"""SPECTER2 embeddings without torch.

The deployed app used to load SPECTER2 through sentence-transformers, which
pulls in torch. Measured on Linux that is ~834MB resident — 440MB of model
weights, 133MB of cross-encoder, ~200MB of torch runtime — against a 512MB
free-tier limit. It OOMed on boot.

Nothing about serving needs torch. Every faculty and paper embedding is
precomputed and pickled; the only thing the model does per request is encode
one short query string. So the model is exported to ONNX, dynamically
quantized to int8 (437MB -> 110MB), and run through onnxruntime, which is
~50MB against torch's ~200MB. Total runtime footprint lands near 340MB.

POOLING MUST MATCH THE CORPUS. sentence-transformers used attention-masked
MEAN pooling followed by L2 normalization for allenai/specter2_base — not CLS
pooling, which is what SPECTER2's paper describes and what a reader would
reasonably assume. Get this wrong and queries land in a different space from
the index: no error, just quietly meaningless rankings. The reference test in
tests/test_onnx_encoder.py pins fp32 ONNX output against sentence-transformers
at cosine >= 0.9999 to keep it that way.
"""
from __future__ import annotations

import os
import threading

import numpy as np

# Where the exported model lives inside the image. Built by
# pipeline/13_export_onnx.py and downloaded during the Docker build.
MODEL_DIR = os.environ.get(
    "ONNX_MODEL_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "onnx_model"),
)

# onnxruntime defaults to every core it can see. On a shared-CPU free instance
# that means heavy thread contention and no speedup, so cap it.
_THREADS = int(os.environ.get("ONNX_THREADS", "2"))

_MAX_TOKENS = 512          # matches sentence-transformers' max_seq_length

_session = None
_tokenizer = None
_input_names: set[str] = set()
_lock = threading.Lock()


def _load():
    """Load lazily and once. Import onnxruntime/tokenizers inside the function
    so that merely importing this module stays cheap for the pipeline scripts,
    which never encode anything."""
    global _session, _tokenizer, _input_names
    if _session is not None:
        return
    with _lock:
        if _session is not None:
            return
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_path = os.path.join(MODEL_DIR, "model_int8.onnx")
        if not os.path.exists(model_path):                      # fp32 fallback
            model_path = os.path.join(MODEL_DIR, "model.onnx")
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"No ONNX model in {MODEL_DIR}. Build one with:\n"
                f"  python3 pipeline/13_export_onnx.py"
            )

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = _THREADS
        opts.inter_op_num_threads = 1
        tok = Tokenizer.from_file(os.path.join(MODEL_DIR, "tokenizer.json"))
        tok.enable_truncation(_MAX_TOKENS)
        tok.enable_padding()

        sess = ort.InferenceSession(
            model_path, opts, providers=["CPUExecutionProvider"])
        _input_names = {i.name for i in sess.get_inputs()}
        _tokenizer, _session = tok, sess


def encode(texts, batch_size: int = 16, normalize_embeddings: bool = True) -> np.ndarray:
    """Embed texts. Signature mirrors SentenceTransformer.encode so callers
    that already hold a model object keep working unchanged."""
    _load()
    if isinstance(texts, str):
        texts = [texts]
    texts = [t if isinstance(t, str) else str(t) for t in texts]
    if not texts:
        return np.empty((0, 768), dtype=np.float32)

    out = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        enc = _tokenizer.encode_batch(chunk)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)

        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in _input_names:
            feed["token_type_ids"] = np.zeros_like(ids)

        tokens = _session.run(None, feed)[0]              # [B, T, 768]

        # Attention-masked mean pooling. Padding must not drag the mean toward
        # zero, hence weighting by the mask rather than a plain .mean(axis=1).
        m = mask[..., None].astype(np.float32)
        summed = (tokens * m).sum(axis=1)
        counts = np.clip(m.sum(axis=1), 1e-9, None)
        vecs = summed / counts

        if normalize_embeddings:
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            vecs = vecs / np.clip(norms, 1e-9, None)
        out.append(vecs.astype(np.float32))

    return np.vstack(out)


class OnnxEncoder:
    """Duck-types the slice of SentenceTransformer that search.py actually uses,
    so load_model() can return this and nothing downstream has to change."""

    def encode(self, sentences, normalize_embeddings: bool = True,
               batch_size: int = 16, show_progress_bar: bool = False, **_):
        return encode(sentences, batch_size=batch_size,
                      normalize_embeddings=normalize_embeddings)

    def __repr__(self):
        return f"OnnxEncoder(dir={MODEL_DIR!r}, threads={_THREADS})"


def is_available() -> bool:
    """True when an exported model is present and onnxruntime is importable."""
    try:
        import onnxruntime  # noqa: F401
        from tokenizers import Tokenizer  # noqa: F401
    except ImportError:
        return False
    return any(
        os.path.exists(os.path.join(MODEL_DIR, f))
        for f in ("model_int8.onnx", "model.onnx")
    )
