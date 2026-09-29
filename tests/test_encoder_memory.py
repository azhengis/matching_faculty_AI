"""How much memory one encode call is allowed to ask for.

These are configuration checks, not model checks: they read constants and the
Dockerfile, so unlike the rest of the ONNX tests they run without an exported
model present. That matters, because the value they guard is what repeatedly
killed a container — and a test that skips on every developer machine would
not have caught it.
"""
import inspect
from pathlib import Path

import onnx_encoder


# ── Batch size is a memory decision, not a throughput one ──────────────────

def test_the_default_batch_keeps_the_attention_tensor_small():
    """Self-attention allocates batch x heads x tokens x tokens x 4 bytes in one
    block. At batch 16 and 512 tokens that is 201MB, on top of the ~110MB model
    and ~150MB of Python — which is what killed a container part-way through
    "Building SPECTER2 embeddings", reporting only "Killed"."""
    heads, tokens, fp32 = 12, onnx_encoder._MAX_TOKENS, 4
    peak_mb = onnx_encoder._BATCH * heads * tokens * tokens * fp32 / 1e6
    assert peak_mb <= 110, (
        f"batch {onnx_encoder._BATCH} peaks at {peak_mb:.0f}MB in a single "
        f"allocation; that is too large for a small container")


def test_the_batch_size_can_be_raised_where_memory_is_plentiful():
    """The CI builder sets ONNX_BATCH=16 to halve the index bake."""
    from pathlib import Path
    dockerfile = (Path(onnx_encoder.__file__).parent / "Dockerfile").read_text()
    assert "ENV ONNX_BATCH=16" in dockerfile
    assert 'os.environ.get("ONNX_BATCH"' in Path(onnx_encoder.__file__).read_text()


def test_an_explicit_batch_size_still_wins():
    """Callers that know their own memory budget must be able to say so."""
    sig = inspect.signature(onnx_encoder.encode)
    assert sig.parameters["batch_size"].default is None, \
        "a hardcoded default would ignore ONNX_BATCH"
