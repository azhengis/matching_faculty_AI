"""The Docker builder stage must carry everything search.py imports.

The runtime stage does `COPY . .`, so it always has the whole repo. The
BUILDER stage is deliberately minimal — it copies a handful of files, bakes the
embedding indexes, and is cached on exactly those inputs.

That minimalism broke production. text_clean.py was added at the repo root and
imported by search.py at module scope, but the builder still copied only
search.py, so the index-baking step died with ModuleNotFoundError and every
deploy after that failed. The live site sat ten commits behind while the code
on main was correct the whole time.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text()


def _builder_stage():
    """Everything between the builder FROM and the runtime FROM."""
    parts = re.split(r"^FROM ", DOCKERFILE, flags=re.MULTILINE)
    # parts[0] is the preamble, parts[1] the builder, parts[2] the runtime.
    assert len(parts) >= 3, "expected a two-stage Dockerfile"
    return parts[1]


def _copied_into_builder():
    """Repo-root files the builder stage copies in."""
    names = set()
    for line in _builder_stage().splitlines():
        m = re.match(r"\s*COPY\s+(.+)$", line)
        if not m or "--from=" in line:
            continue
        # Last token is the destination; the rest are sources.
        tokens = m.group(1).split()
        for src in tokens[:-1]:
            names.add(Path(src).name)
    return names


def _root_modules_imported_by(filename):
    """MODULE-SCOPE imports of `filename` resolving to a repo-root .py file.

    Only module scope counts. search.py imports onnx_encoder lazily inside
    load_model(), which the builder never calls, so that file genuinely does
    not need to be present. An import at module scope has to be satisfiable
    the moment the module is imported, which is what the builder does.
    """
    tree = ast.parse((ROOT / filename).read_text())
    root_modules = {p.stem for p in ROOT.glob("*.py")}
    needed = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            base = node.module.split(".")[0]
            if base in root_modules:
                needed.add(base)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                base = alias.name.split(".")[0]
                if base in root_modules:
                    needed.add(base)
    return needed


def test_the_builder_copies_every_root_module_search_imports():
    """The exact failure: search.py imports text_clean, the builder did not
    copy it, and the index-baking step could not import search at all."""
    copied = _copied_into_builder()
    missing = sorted(
        m for m in _root_modules_imported_by("search.py")
        if f"{m}.py" not in copied
    )
    assert missing == [], (
        f"Dockerfile builder stage does not COPY {missing}, which search.py "
        f"imports at module scope. The index-baking step will fail with "
        f"ModuleNotFoundError and the deploy will stop."
    )


def test_search_itself_is_copied():
    """Guards the guard: if search.py stopped being copied the test above
    would pass vacuously, since it only checks that stage's imports."""
    assert "search.py" in _copied_into_builder()


def test_the_seed_the_image_is_built_from_is_copied():
    assert "seed_faculty.db.gz" in _copied_into_builder()
