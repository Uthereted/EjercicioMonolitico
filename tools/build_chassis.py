"""Build the consumer's local chassis wheel from its independent source checkout."""
import argparse
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, default=root.parent / 'chasis_homework')
args = parser.parse_args()
source = args.source.resolve()
if not (source / 'setup.py').is_file():
    parser.error('Source must be the chassis repository directory')
(root / 'vendor').mkdir(exist_ok=True)
subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-index',
                '--no-build-isolation', '--wheel-dir', str(root / 'vendor'), str(source)], check=True)
