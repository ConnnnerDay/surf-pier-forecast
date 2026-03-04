from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {'.git', '.venv', '__pycache__'}
CHECK_EXTENSIONS = {'.py', '.html', '.css', '.js', '.md', '.txt', '.json', '.yml', '.yaml'}
MARKERS = ('<<<<<<< ', '=======', '>>>>>>> ')


def iter_files():
    for path in ROOT.rglob('*'):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in CHECK_EXTENSIONS:
            continue
        yield path


def test_repo_has_no_merge_conflict_markers():
    offenders = []
    for path in iter_files():
        text = path.read_text(encoding='utf-8', errors='ignore')
        lines = text.splitlines()
        for idx, line in enumerate(lines, start=1):
            if line.startswith(MARKERS):
                offenders.append(f"{path.relative_to(ROOT)}:{idx}: {line[:20]}")

    assert not offenders, "Merge conflict markers found:\n" + "\n".join(offenders)
