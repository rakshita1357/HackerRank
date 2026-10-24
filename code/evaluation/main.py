#!/usr/bin/env python3
"""Evaluation harness: public samples, hand-built custom cases and consistency checks.

    python3 code/evaluation/main.py            # print metrics and regenerate evaluation/evaluation_report.md
    python3 code/evaluation/main.py --no-report
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
ROOT = os.path.dirname(CODE)
sys.path.insert(0, CODE)
sys.path.insert(0, HERE)

from ghost.engine import Auditor, World, case_no  # noqa: E402
from ghost.scoring import FIELDS, score  # noqa: E402
import custom_cases  # noqa: E402


def to_output(cases) -> Dict[str, Tuple[str, List[dict]]]:
    out = {}
    for c in cases:
        out[c.case_id] = (c.status, [{k: s[k] for k in FIELDS} for s in c.steps])
    return out


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, encoding='utf-8', newline='') as fh:
        return list(csv.DictReader(fh))


def load_gold(path: str) -> Dict[str, Tuple[str, List[dict]]]:
    return {r['case_id']: (r['deletion_status'], json.loads(r['steps_json'] or '[]')) for r in read_csv(path)}


def samples_metrics() -> Tuple[Dict[str, float], Dict[str, Tuple[str, List[dict]]], Dict[str, Tuple[str, List[dict]]]]:
    ds = os.path.join(ROOT, 'dataset')
    samples = read_csv(os.path.join(ds, 'sample_deletion_requests.csv'))
    evals = read_csv(os.path.join(ds, 'deletion_requests.csv'))
    world = World(ds)
    # no seeding: the sample answers are the held-out labels here
    cases = Auditor(world).run(samples + evals, seeds=None)
    pred = to_output([c for c in cases if c.case_id in {s['case_id'] for s in samples}])
    gold = load_gold(os.path.join(ds, 'sample_output.csv'))
    return score(pred, gold), pred, gold


def custom_metrics():
    d, requests, expected = custom_cases.build()
    world = World(d)
    cases = Auditor(world).run(requests)
    pred = to_output(cases)
    per = {}
    for cid, (st, steps) in expected.items():
        ps, psteps = pred[cid]
        per[cid] = {'status': ps == st, 'steps_exact': psteps == steps, 'expected': (st, steps), 'got': (ps, psteps)}
    return score(pred, expected), per


def activity_consistency() -> Dict[str, int]:
    """Cross-check evidence-derived states against what the (untrusted) workflow log implies.

    A compliant resource should be described by a truthful "Policy action completed" row; a resource we flag
    as violating must never carry one.  Disagreements point at a mis-read evidence format."""
    ds = os.path.join(ROOT, 'dataset')
    rows = read_csv(os.path.join(ds, 'sample_deletion_requests.csv')) + read_csv(os.path.join(ds, 'deletion_requests.csv'))
    world = World(ds)
    auditor = Auditor(world)
    cases = auditor.run(rows)
    res = {'ambiguous_assignments': len(auditor.ties), 'flagged_violating': 0, 'flagged_with_policy_completed': 0, 'compliant_with_final_result_claim': 0,
           'compliant_checked': 0}
    for c in cases:
        for rid, st in c.states.items():
            acts = [x for x in world.act_by_rid.get(rid, []) if c.row['requested_at'] <= x['event_time'] <= c.row['audit_at']]
            done = any('Policy action completed' in x['details'] for x in acts)
            if st.violation:
                res['flagged_violating'] += 1
                res['flagged_with_policy_completed'] += done
            elif st.prefix in ('support', 'analytics', 'backup', 'data_flow', 'tombstone'):
                res['compliant_checked'] += 1
                res['compliant_with_final_result_claim'] += any('received the final result' in x['details'] for x in acts)
    return res


def llm_smoke() -> Dict[str, object]:
    """Exercise the Gemini path (cache, ledger, usage report) with a fake client - no network, no key."""
    import tempfile
    from types import SimpleNamespace
    from ghost.llm import LLM
    from ghost import usage

    calls = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            calls.append(model)
            text = json.dumps({'deletion_requested': 'delete' in str(contents[0]).lower()})
            return SimpleNamespace(text=text, usage_metadata=SimpleNamespace(
                prompt_token_count=120, candidates_token_count=10, thoughts_token_count=0))

    llm = LLM(model='gemini-2.5-flash', cache_dir=tempfile.mkdtemp(prefix='ghost_llm_'), enabled=True)
    llm.enabled = True
    llm._client = SimpleNamespace(models=FakeModels())
    a = llm.classify_request('Please delete my account')
    b = llm.classify_request('Please delete my account')          # second call must be a cache replay
    c = llm.classify_request('Send me a copy of my data')
    md = usage.render(llm.ledger, 3, llm.model, True)
    return {'verdicts': [a, b, c], 'live_calls': len(calls), 'ledger_entries': len(llm.ledger),
            'cached_entries': sum(1 for e in llm.ledger if e['cached']), 'report_has_model': 'gemini-2.5-flash' in md,
            'ok': [a, b, c] == [True, True, False] and len(calls) == 2}


def fmt(m: Dict[str, float]) -> str:
    keys = ['status_accuracy', 'step_precision', 'step_recall', 'step_f1', 'sequence_exact', 'sequence_lcs', 'row_exact']
    return ', '.join(f'{k}={m[k]:.3f}' for k in keys)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-report', action='store_true')
    args = ap.parse_args(argv)
    t0 = time.time()
    sm, pred, gold = samples_metrics()
    cm, per = custom_metrics()
    cons = activity_consistency()
    smoke = llm_smoke()
    print('public samples :', fmt(sm))
    print('custom cases   :', fmt(cm))
    for cid, r in per.items():
        print(f"  {cid}: status_ok={r['status']} steps_exact={r['steps_exact']}")
    print('activity consistency:', cons)
    print('LLM layer smoke test (fake client):', smoke)
    if not args.no_report:
        import report
        report.write(os.path.join(HERE, 'evaluation_report.md'), sm, pred, gold, cm, per, cons, smoke)
        print('wrote', os.path.join(HERE, 'evaluation_report.md'))
    print(f'({time.time() - t0:.1f}s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
