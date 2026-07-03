#!/usr/bin/env python3
"""
10_finetune_specter2.py
-----------------------
Fine-tune SPECTER2 on DePaul faculty data using 3 different configurations,
then evaluate each on a held-out test set. Keeps the best model.

Training approach — MultipleNegativesRankingLoss:
  Given a batch of (query, faculty_bio) pairs, each query should be closest
  to its own faculty bio and farther from all other bios in the batch.
  No explicit negatives needed — everything else in the batch is a negative.
  This is the same technique used to train sentence-BERT and many retrieval models.

3 configs compared:
  A — fast:        1 epoch,  lr=2e-5  (quick baseline, may underfit)
  B — standard:    3 epochs, lr=2e-5  (typical setting, usually best)
  C — conservative: 3 epochs, lr=5e-6  (very slow updates, more stable)

The winning config is saved to models/specter2_depaul_finetuned/
and can be plugged into search.py by setting FINETUNED_MODEL env var.

Requirements:
  pip3 install --break-system-packages --user sentence-transformers datasets

Run:
  python3 pipeline/10_finetune_specter2.py

Time estimate (CPU):
  ~443 faculty × 5 queries = 2215 training pairs
  Config A: ~15-25 min
  Config B: ~45-75 min
  Config C: ~45-75 min
  Total: 1.5-3 hours on CPU, 20-30 min on GPU
"""
import os, sys, json, time
import numpy as np

_ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(_ROOT, "data", "training_pairs.json")
MODELS_DIR = os.path.join(_ROOT, "models")
BASE_MODEL = "allenai/specter2_base"

# ── Test queries with known correct faculty (held-out evaluation set) ─────────
# Format: {"query": ..., "expected": [list of faculty names that should appear]}
# Adjust these to match your actual faculty roster.
TEST_QUERIES = [
    {
        "query":    "Alzheimer disease neurodegeneration amyloid protein",
        "expected": ["Eric Norstrom", "Eiron Cudaback"],
    },
    {
        "query":    "machine learning clinical decision support healthcare",
        "expected": ["Casey Bennett"],
    },
    {
        "query":    "natural language processing text classification sentiment",
        "expected": ["Noriko Tomuro"],
    },
    {
        "query":    "network security intrusion detection anomaly",
        "expected": ["Jean-Philippe Labruyere"],
    },
    {
        "query":    "corporate finance risk management investment",
        "expected": [],   # no expected set — still tests that results are plausible
    },
    {
        "query":    "deep learning computer vision image segmentation",
        "expected": ["Daniela Stan Raicu"],
    },
    {
        "query":    "bilingual language acquisition children second language",
        "expected": [],
    },
    {
        "query":    "social justice equity marginalized communities",
        "expected": [],
    },
]


# ── Training configs ──────────────────────────────────────────────────────────
CONFIGS = [
    {"name": "A_fast",         "epochs": 1, "lr": 2e-5, "batch": 16},
    {"name": "B_standard",     "epochs": 3, "lr": 2e-5, "batch": 16},
    {"name": "C_conservative", "epochs": 3, "lr": 5e-6, "batch": 16},
]


def load_training_data():
    if not os.path.exists(DATA_FILE):
        sys.exit(
            f"Training data not found at {DATA_FILE}.\n"
            "Run pipeline/9_generate_training_data.py first."
        )
    with open(DATA_FILE) as f:
        pairs = json.load(f)
    print(f"Loaded {len(pairs)} training pairs from {len({p['faculty_id'] for p in pairs})} faculty")
    return pairs


def load_faculty_bios():
    """Load all searchable faculty bios for evaluation."""
    import sqlite3
    con = sqlite3.connect(os.path.join(_ROOT, "faculty.db"))
    rows = con.execute("""
        SELECT name, COALESCE(research_summary, classes_taught, '') as bio
        FROM faculty
        WHERE TRIM(COALESCE(research_summary,'')) != ''
           OR TRIM(COALESCE(classes_taught,'')) != ''
    """).fetchall()
    con.close()
    return [(name, bio[:600]) for name, bio in rows if bio.strip()]


def evaluate(model, faculty_bios: list, test_queries: list, label: str) -> float:
    """Compute precision@5 on held-out test queries with expected results.
    Queries without 'expected' contribute 0 to the total (skipped).
    Returns mean precision@5 across queries with known expected results.
    """
    from sentence_transformers import SentenceTransformer
    names  = [name for name, _ in faculty_bios]
    bios   = [bio  for _, bio  in faculty_bios]

    print(f"\n  Evaluating {label}...")
    bio_embs = model.encode(bios, normalize_embeddings=True, show_progress_bar=False,
                             batch_size=32)

    scores_list = []
    for item in test_queries:
        q        = item["query"]
        expected = item.get("expected", [])
        if not expected:
            continue

        qv    = model.encode([q], normalize_embeddings=True)[0]
        sims  = bio_embs @ qv
        top5  = np.argsort(sims)[::-1][:5]
        found = [names[i] for i in top5]
        hits  = sum(1 for name in expected if name in found)
        p5    = hits / max(len(expected), 1)
        scores_list.append(p5)
        status = "✓" if hits > 0 else "✗"
        print(f"    {status}  [{q[:45]}...]  top5={found[:3]}")

    if not scores_list:
        print("    (no expected results specified — skipping precision calc)")
        return 0.0
    mean_p5 = sum(scores_list) / len(scores_list)
    print(f"  → {label}: precision@5 = {mean_p5:.3f}  (over {len(scores_list)} queries)")
    return mean_p5


def finetune_config(cfg: dict, train_pairs: list, faculty_bios: list) -> float:
    import torch
    from sentence_transformers import SentenceTransformer, SentenceTransformerTrainer, SentenceTransformerTrainingArguments
    from sentence_transformers.losses import MultipleNegativesRankingLoss
    from datasets import Dataset

    name   = cfg["name"]
    epochs = cfg["epochs"]
    lr     = cfg["lr"]
    batch  = cfg["batch"]
    out    = os.path.join(MODELS_DIR, f"specter2_depaul_{name}")

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"\n{'='*60}")
    print(f"Config {name}: {epochs} epoch(s), lr={lr}, batch={batch}, device={device}")
    print(f"{'='*60}")

    model = SentenceTransformer(BASE_MODEL, device=device)
    loss  = MultipleNegativesRankingLoss(model)

    train_dataset = Dataset.from_dict({
        "anchor":   [p["query"] for p in train_pairs],
        "positive": [p["bio"]   for p in train_pairs],
    })

    train_args = SentenceTransformerTrainingArguments(
        output_dir=out,
        num_train_epochs=epochs,
        per_device_train_batch_size=batch,
        learning_rate=lr,
        save_strategy="no",
    )

    start   = time.time()
    trainer = SentenceTransformerTrainer(model=model, args=train_args, train_dataset=train_dataset, loss=loss)
    trainer.train()
    model.save(out)
    elapsed = time.time() - start
    print(f"  Training done in {elapsed/60:.1f} min — saved to {out}")

    p5 = evaluate(model, faculty_bios, TEST_QUERIES, label=name)
    return p5


def evaluate_baseline(faculty_bios: list) -> float:
    import torch
    from sentence_transformers import SentenceTransformer
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print("\n" + "="*60)
    print("Baseline: allenai/specter2_base (no fine-tuning)")
    print("="*60)
    model = SentenceTransformer(BASE_MODEL, device=device)
    return evaluate(model, faculty_bios, TEST_QUERIES, label="baseline")


def main():
    try:
        from sentence_transformers import SentenceTransformer, SentenceTransformerTrainer, SentenceTransformerTrainingArguments
    except ImportError:
        sys.exit(
            "Install sentence-transformers first:\n"
            "  pip3 install --break-system-packages --user sentence-transformers"
        )

    os.makedirs(MODELS_DIR, exist_ok=True)

    train_pairs   = load_training_data()
    faculty_bios  = load_faculty_bios()
    print(f"Evaluation set: {len(faculty_bios)} faculty bios, "
          f"{len([q for q in TEST_QUERIES if q.get('expected')])} queries with expected results")

    results = {}

    # Baseline (no fine-tuning)
    results["baseline"] = evaluate_baseline(faculty_bios)

    # Fine-tune each config
    for cfg in CONFIGS:
        p5 = finetune_config(cfg, train_pairs, faculty_bios)
        results[cfg["name"]] = p5

    # Summary
    print("\n" + "="*60)
    print("COMPARISON RESULTS (precision@5)")
    print("="*60)
    best_name = max(results, key=results.get)
    for name, p5 in results.items():
        marker = " ← BEST" if name == best_name else ""
        print(f"  {name:25s}  {p5:.3f}{marker}")

    # Save the best fine-tuned model as the recommended one
    best_non_baseline = max(
        [(n, p) for n, p in results.items() if n != "baseline"],
        key=lambda x: x[1],
    )
    best_cfg_name, best_p5 = best_non_baseline
    if best_p5 > results["baseline"]:
        best_src = os.path.join(MODELS_DIR, f"specter2_depaul_{best_cfg_name}")
        print(f"\n  Best fine-tuned config: {best_cfg_name} (precision@5 = {best_p5:.3f})")
        print(f"  Improvement over baseline: +{best_p5 - results['baseline']:.3f}")
        print(f"\nTo use the fine-tuned model, set:")
        print(f"  export FINETUNED_MODEL={best_src}")
        print(f"  python3 search.py")
    else:
        print("\n  None of the fine-tuned configs beat baseline.")
        print("  Consider: more training data, more epochs, or different hyperparameters.")

    with open(os.path.join(MODELS_DIR, "comparison_results.json"), "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results saved to models/comparison_results.json")


if __name__ == "__main__":
    main()
