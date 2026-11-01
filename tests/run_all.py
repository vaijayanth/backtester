"""Runs every Python test file in tests/ (no pytest needed). Usage: python tests/run_all.py"""
import subprocess
import sys
from pathlib import Path

failed = []
for f in sorted(Path(__file__).parent.glob("test_*.py")):
    r = subprocess.run([sys.executable, str(f)], capture_output=True, text=True)
    print((r.stdout.strip() or r.stderr.strip().splitlines()[-1] if r.returncode else r.stdout.strip()))
    if r.returncode:
        failed.append(f.name)
        print(r.stderr)
sys.exit(1 if failed else 0)
