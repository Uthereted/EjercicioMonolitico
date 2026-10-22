"""Create the submission ZIP using only chassis sources and documentation."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
source = root / "chassis"
destination = root / "deliverables" / "chassis.zip"
destination.parent.mkdir(exist_ok=True)
files = [source / name for name in ("pyproject.toml", "README.md", ".gitignore")]
for folder in ("manufacturing_chassis", "tests"):
    files.extend(path for path in (source / folder).rglob("*.py") if "__pycache__" not in path.parts)
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for path in sorted(files):
        archive.write(path, path.relative_to(source).as_posix())
print(destination)
