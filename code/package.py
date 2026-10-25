#!/usr/bin/env python3
"""Build code.zip: solution, prompts, config, README and evaluation reports - no dataset, caches or secrets."""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKIP_DIRS = {'__pycache__', '.cache', '.git', '.venv', 'venv', 'env', 'node_modules', 'build', 'dist', 'dataset', 'data', '.idea'}
SKIP_EXT = {'.pyc', '.zip'}


def main() -> None:
    os.makedirs(os.path.join(ROOT, 'submission'), exist_ok=True)
    out = os.path.join(ROOT, 'submission', 'code.zip')
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for base, dirs, files in os.walk(HERE):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for f in sorted(files):
                if os.path.splitext(f)[1] in SKIP_EXT or (f.startswith('.env') and f != '.env.example'):
                    continue
                p = os.path.join(base, f)
                z.write(p, os.path.relpath(p, ROOT))
    print('wrote', out)


if __name__ == '__main__':
    main()
