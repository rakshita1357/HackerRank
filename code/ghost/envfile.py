"""Minimal .env loader (stdlib only). Values already present in the process environment win."""
from __future__ import annotations

import os
from typing import List

HERE = os.path.dirname(os.path.abspath(__file__))
CODE_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(CODE_DIR)


def load_env(paths: List[str] | None = None) -> List[str]:
    """Read KEY=VALUE lines from .env files (repo root, then code/). Returns the files that were read."""
    read = []
    for p in paths or [os.path.join(os.getcwd(), '.env'), os.path.join(ROOT, '.env'), os.path.join(CODE_DIR, '.env')]:
        if not os.path.isfile(p) or p in read:
            continue
        with open(p, encoding='utf-8-sig') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                if line.startswith('export '):
                    line = line[7:].lstrip()
                k, v = line.split('=', 1)
                k, v = k.strip(), v.strip()
                if v[:1] in ('"', "'") and v.find(v[0], 1) > 0:
                    v = v[1:v.find(v[0], 1)]          # quoted value; anything after the closing quote is a comment
                elif ' #' in v:
                    v = v.split(' #', 1)[0].strip()
                if k and v and k not in os.environ:
                    os.environ[k] = v
        read.append(p)
    return read
