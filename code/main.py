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
from ghost.llm import LLM  # noqa: E402
from ghost import usage  # noqa: E402
from ghost.validate import validate  # noqa: E402


def main(argv=None) -> int:
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root (folder that holds code/ and dataset/)
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', default=os.path.join(ROOT, 'dataset'))
    ap.add_argument('--requests', default=None, help='requests CSV (default: dataset/deletion_requests.csv)')
    ap.add_argument('--out', default=os.path.join(ROOT, 'output.csv'))
    ap.add_argument('--debug', default=None, help='write per-case debug json here')
    ap.add_argument('--no-samples', action='store_true', help='do not co-process the public sample cases')
    ap.add_argument('--no-seed', action='store_true', help='co-process samples but ignore their published answers')
    ap.add_argument('--llm', choices=['auto', 'on', 'off'], default='auto',
                    help='Gemini layer: auto = use it when GEMINI_API_KEY is set (default), off = fully deterministic')
    ap.add_argument('--usage-report', default=None, help='where to write the usage report (default: code/evaluation/usage_report.md for full runs)')
    args = ap.parse_args(argv)
    if not os.path.isdir(args.dataset):
        print(f'ERROR: dataset folder not found: {args.dataset}\n  Put the dataset next to code/ (repo root/dataset) or pass --dataset PATH', file=sys.stderr)
        return 2
    req = args.requests or os.path.join(args.dataset, 'deletion_requests.csv')
    with open(req, encoding='utf-8', newline='') as fh:
        rows = list(csv.DictReader(fh))
    llm = LLM(enabled=args.llm != 'off')
    use_llm = llm.enabled and args.llm != 'off'
    world = World(args.dataset, llm.artifact_facts if use_llm else None)
    if use_llm:
        print(f'Gemini enabled ({llm.model}); classifying {len(rows)} requests (cached after the first run)...', file=sys.stderr, flush=True)
        llm.prefetch_classify([r['request_text'] for r in rows])
    # The 32 public sample cases compete for the same activity rows as the evaluation cases, so they are
    # processed together (and their published answers tell us which resources they already own).
    wanted = {r['case_id'] for r in rows}
    extra, seeds = [], {}
    sample_req = os.path.join(args.dataset, 'sample_deletion_requests.csv')
    sample_out = os.path.join(args.dataset, 'sample_output.csv')
    if not args.no_samples and os.path.exists(sample_req):
        with open(sample_req, encoding='utf-8', newline='') as fh:
            extra = [r for r in csv.DictReader(fh) if r['case_id'] not in wanted]
        if os.path.exists(sample_out) and not args.no_seed:
            with open(sample_out, encoding='utf-8', newline='') as fh:
                for r in csv.DictReader(fh):
                    seeds[r['case_id']] = {s['resource_id'] for s in json.loads(r['steps_json'] or '[]')}
    cases = Auditor(world, llm.classify_request if use_llm else None,
                    llm.classify_request if use_llm else None).run(rows + extra, seeds)
    cases = [c for c in cases if c.case_id in wanted]
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
    problems = validate(args.out, [r['case_id'] for r in rows])
    for p in problems[:20]:
        print('VALIDATION:', p, file=sys.stderr)
    report = args.usage_report or (os.path.join(HERE, 'evaluation', 'usage_report.md') if args.requests is None else None)
    if report:
        os.makedirs(os.path.dirname(report), exist_ok=True)
        with open(report, 'w', encoding='utf-8') as fh:
            fh.write(usage.render(llm.ledger, len(rows), llm.model, use_llm))
    print(f'wrote {len(cases)} rows to {args.out}' + (f' (LLM calls: {len(llm.ledger)})' if use_llm else ''))
    return 1 if problems else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        import platform, traceback
        traceback.print_exc()
        print(f'\n--- send this block when reporting a problem ---\npython {platform.python_version()} on {platform.platform()}\ncwd={os.getcwd()}\nfile={os.path.abspath(__file__)}', file=sys.stderr)
        raise SystemExit(1)
