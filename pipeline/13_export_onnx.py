#!/usr/bin/env python3
"""
13_export_onnx.py
-----------------
Export SPECTER2 to ONNX and quantize it to int8, so the deployed app can embed
queries without torch.

Why: serving used to load SPECTER2 through sentence-transformers, which drags
in torch. On Linux that measures ~834MB resident against a 512MB free-tier
limit, and the container OOMs on boot. Nothing about serving needs torch —
every corpus embedding is precomputed — so the only runtime job is encoding one
short query, which onnxruntime does in ~50MB instead of torch's ~200MB.

    fp32 ONNX : ~437 MB   (bit-identical to sentence-transformers)
    int8 ONNX : ~110 MB   (cosine ~0.994 against fp32)

The indexes are NOT rebuilt with the quantized model, which is the opposite
of what I first assumed. Measured over a 200-faculty sample against ten
queries:

    fp32 index + int8 query   92% top-5 overlap, 10/10 top-1   <- shipped
    int8 index + int8 query   88% top-5 overlap, 10/10 top-1

Quantizing only the query perturbs one side; rebuilding perturbs both and
compounds the noise. So the committed indexes stay exactly as
sentence-transformers built them.

This runs at BUILD time and needs torch + optimum. The deployed image does not.

    python3 pipeline/13_export_onnx.py                 # -> onnx_model/
    python3 pipeline/13_export_onnx.py --no-quantize   # fp32 only
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

MODEL_ID = "allenai/specter2_base"
DEFAULT_OUT = os.path.join(ROOT, "onnx_model")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-quantize", action="store_true")
    args = ap.parse_args()

    try:
        from optimum.onnxruntime import ORTModelForFeatureExtraction
        from transformers import AutoTokenizer
    except ImportError:
        sys.exit("Needs the build extras:\n"
                 "  pip install -r requirements-build.txt")

    os.makedirs(args.out, exist_ok=True)

    print(f"Exporting {MODEL_ID} to ONNX (this pulls the fp32 weights)...")
    model = ORTModelForFeatureExtraction.from_pretrained(MODEL_ID, export=True)
    model.save_pretrained(args.out)
    AutoTokenizer.from_pretrained(MODEL_ID).save_pretrained(args.out)

    fp32 = os.path.join(args.out, "model.onnx")
    print(f"  fp32: {os.path.getsize(fp32)/1e6:.1f} MB")

    if not args.no_quantize:
        from onnxruntime.quantization import quantize_dynamic, QuantType
        int8 = os.path.join(args.out, "model_int8.onnx")
        print("Quantizing to int8 (weights only; activations stay float)...")
        quantize_dynamic(fp32, int8, weight_type=QuantType.QInt8,
                         extra_options={"MatMulConstBOnly": True})
        print(f"  int8: {os.path.getsize(int8)/1e6:.1f} MB")

        # The fp32 graph is 437MB and the runtime prefers int8 when both are
        # present. Keeping it would quadruple the image for nothing.
        os.remove(fp32)
        print("  removed the fp32 graph (the image ships int8 only)")

    # Trim anything the runtime does not read. tokenizers loads tokenizer.json
    # alone; vocab.txt and the slow-tokenizer configs are dead weight.
    for junk in ("vocab.txt", "tokenizer_config.json", "special_tokens_map.json"):
        p = os.path.join(args.out, junk)
        if os.path.exists(p):
            os.remove(p)

    total = sum(os.path.getsize(os.path.join(args.out, f))
                for f in os.listdir(args.out))
    print(f"\nWrote {args.out}  ({total/1e6:.1f} MB total)")
    for f in sorted(os.listdir(args.out)):
        print(f"  {os.path.getsize(os.path.join(args.out, f))/1e6:8.2f} MB  {f}")

    print("\nThe existing indexes stay as they are — see the note at the top of\n"
          "this file on why rebuilding them in int8 measures slightly worse.")


if __name__ == "__main__":
    main()
