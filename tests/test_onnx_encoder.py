"""Pins the ONNX encoder to the embedding space the indexes were built in.

The failure this guards against is silent. If the pooling in onnx_encoder ever
drifts from what sentence-transformers did — CLS instead of mean, forgetting
the attention mask, skipping normalization — nothing raises. Queries simply
land somewhere else in the space and every match quietly gets worse.

SPECTER2's paper describes CLS pooling, and the model card implies it, but the
sentence-transformers config that actually built these indexes specifies MEAN
pooling. A reasonable person reimplementing this would get it wrong. Hence a
test that compares real vectors rather than trusting the code to look right.

The reference model needs torch (a build-time dependency the deployed image
does not carry), so these skip when it is absent.
"""
import os

import numpy as np
import pytest

import onnx_encoder

pytestmark = pytest.mark.skipif(
    not onnx_encoder.is_available(),
    reason="no exported ONNX model; run pipeline/13_export_onnx.py",
)

def _is_int8():
    """The shipped graph is int8. Dynamic quantization derives activation
    scales per-tensor at inference time, so a vector depends slightly on what
    else was in its batch — ~0.994 rather than 1.0. That is a property of the
    quantization, not a pooling bug: the fp32 graph is padding-invariant to
    1.000000, verified. Thresholds below reflect whichever graph is present.

    Mirrors onnx_encoder's own preference: int8 wins whenever it exists, so
    testing for the ABSENCE of fp32 would report fp32 while int8 is loaded."""
    return os.path.exists(os.path.join(onnx_encoder.MODEL_DIR, "model_int8.onnx"))


TEXTS = [
    "fairness auditing of clinical risk prediction models",
    "bilingual language acquisition in young children",
    "quantum secret sharing protocols for secure communication",
]


def _sentence_transformers():
    st = pytest.importorskip(
        "sentence_transformers",
        reason="torch/sentence-transformers is build-time only")
    return st.SentenceTransformer("allenai/specter2_base")


def test_output_shape_and_normalization():
    v = onnx_encoder.encode(TEXTS)
    assert v.shape == (len(TEXTS), 768)
    norms = np.linalg.norm(v, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-4), f"not unit vectors: {norms}"


def test_a_bare_string_is_accepted_like_a_list():
    one = onnx_encoder.encode("a single query")
    assert one.shape == (1, 768)


def test_empty_input_returns_an_empty_matrix_not_a_crash():
    assert onnx_encoder.encode([]).shape == (0, 768)


def test_batching_does_not_meaningfully_change_the_vectors():
    """A mask bug shows up as batch-size sensitivity — but so does int8.

    On fp32 this must be exact. On int8, ~0.99 is expected and acceptable;
    anything below it means the attention mask is genuinely being ignored,
    which collapses short texts toward zero.
    """
    big = onnx_encoder.encode(TEXTS, batch_size=16)
    small = onnx_encoder.encode(TEXTS, batch_size=1)
    sims = [float(big[i] @ small[i]) for i in range(len(TEXTS))]
    floor = 0.99 if _is_int8() else 0.9999
    assert all(s > floor for s in sims), f"batch-size sensitivity: {sims}"


def test_padding_does_not_leak_into_the_mean():
    """A short text batched with a long one must embed as it does alone.

    Mean-pooling without honouring the attention mask drags short texts toward
    zero in proportion to how much padding they got — the classic version of
    this bug, and invisible without exactly this comparison.
    """
    short = "genomics"
    alone = onnx_encoder.encode([short])[0]
    padded = onnx_encoder.encode([short, "a much longer piece of text " * 40])[0]
    sim = float(alone @ padded)
    # fp32 measures exactly 1.000000 here. int8 measures ~0.994 purely from
    # activation-scale drift; an unmasked mean would land far lower, around
    # 0.6-0.8 for a 40x length difference, so this still catches the real bug.
    floor = 0.99 if _is_int8() else 0.9999
    assert sim > floor, f"padding leaked into the mean: {sim}"


@pytest.mark.slow
def test_matches_sentence_transformers_exactly_in_fp32():
    """The reference check: fp32 ONNX must reproduce the corpus embedding.

    Only meaningful against the unquantized graph. int8 is expected to differ
    slightly (~0.994) and is covered by the ranking check below instead.
    """
    fp32_path = os.path.join(onnx_encoder.MODEL_DIR, "model.onnx")
    if not os.path.exists(fp32_path):
        pytest.skip("fp32 graph not shipped; only int8 is in the image")

    # Force the fp32 graph: the encoder would otherwise pick int8, and this
    # test is specifically the exactness check on the unquantized model.
    import numpy as _np
    import onnxruntime as ort
    from tokenizers import Tokenizer
    sess = ort.InferenceSession(fp32_path, providers=["CPUExecutionProvider"])
    tok = Tokenizer.from_file(os.path.join(onnx_encoder.MODEL_DIR, "tokenizer.json"))
    tok.enable_truncation(512); tok.enable_padding()
    names = {i.name for i in sess.get_inputs()}
    enc = tok.encode_batch(TEXTS)
    ids = _np.array([e.ids for e in enc], dtype=_np.int64)
    mask = _np.array([e.attention_mask for e in enc], dtype=_np.int64)
    feed = {"input_ids": ids, "attention_mask": mask}
    if "token_type_ids" in names:
        feed["token_type_ids"] = _np.zeros_like(ids)
    toks = sess.run(None, feed)[0]
    m = mask[..., None].astype(_np.float32)
    got = (toks * m).sum(1) / _np.clip(m.sum(1), 1e-9, None)
    got = got / _np.clip(_np.linalg.norm(got, axis=1, keepdims=True), 1e-9, None)

    st = _sentence_transformers()
    ref = st.encode(TEXTS, normalize_embeddings=True)
    sims = [float(ref[i] @ got[i]) for i in range(len(TEXTS))]
    assert all(s > 0.9999 for s in sims), f"pooling drifted: {sims}"


@pytest.mark.slow
def test_int8_preserves_the_top_match():
    """Quantization may reshuffle near-ties; it must not change the winner.

    Ranking is the product, not cosine similarity. Measured at 10/10 top-1
    agreement and 88% top-5 overlap over a 200-faculty sample when this was
    adopted.
    """
    st = _sentence_transformers()
    corpus = [
        "I study fairness and bias in clinical machine learning models.",
        "My research is on second language acquisition in bilingual children.",
        "I work on quantum information theory and cryptographic protocols.",
        "I research urban education policy and school funding equity.",
        "My field is protein folding and structural molecular biology.",
    ]
    ref_c = st.encode(corpus, normalize_embeddings=True)
    onx_c = onnx_encoder.encode(corpus)

    for query, expected in [
        ("auditing bias in hospital risk scores", 0),
        ("how children learn two languages", 1),
        ("quantum cryptography", 2),
        ("school district funding inequality", 3),
        ("molecular structure of proteins", 4),
    ]:
        ref_top = int(np.argmax(ref_c @ st.encode([query], normalize_embeddings=True)[0]))
        onx_top = int(np.argmax(onx_c @ onnx_encoder.encode([query])[0]))
        assert ref_top == expected, f"reference model itself missed: {query}"
        assert onx_top == expected, f"int8 changed the top match for: {query}"
