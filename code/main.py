#!/usr/bin/env python3
"""Ghost Data Still Exists - deletion audit agent.

Usage:  python3 code/main.py [--dataset DIR] [--out FILE] [--requests FILE] [--no-llm]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)

from ghost.engine import Auditor, World, case_no  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', default=os.path.join(ROOT, 'dataset'))
    ap.add_argument('--requests', default=None, help='requests CSV (default: dataset/deletion_requests.csv)')
    ap.add_argument('--out', default=os.path.join(ROOT, 'output.csv'))
    ap.add_argument('--debug', default=None, help='write per-case debug json here')
    args = ap.parse_args(argv)
    req = args.requests or os.path.join(args.dataset, 'deletion_requests.csv')
    with open(req, encoding='utf-8', newline='') as fh:
        rows = list(csv.DictReader(fh))
    world = World(args.dataset)
    cases = Auditor(world).run(rows)
    cases.sort(key=lambda c: c.case_id)
    with open(args.out, 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['case_id', 'deletion_status', 'steps_json'])
        for c in cases:
            steps = [{k: s[k] for k in ('step_no', 'phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id')}
                     for s in c.steps]
            w.writerow([c.case_id, c.status, json.dumps(steps, separators=(',', ':'), ensure_ascii=False)])
    if args.debug:
        with open(args.debug, 'w', encoding='utf-8') as fh:
            json.dump({c.case_id: {'rids': c.rids, 'debug': c.debug} for c in cases}, fh, indent=1)
    print(f'wrote {len(cases)} rows to {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
