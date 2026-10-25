#!/usr/bin/env python3
"""Run the 16 custom / edge cases against the pipeline.

    python code/evaluation/custom_suite.py                    # build in a temp folder, run, print PASS/FAIL per case
    python code/evaluation/custom_suite.py --out my_suite     # keep the generated mini-dataset + expected_output.csv to inspect
    python code/evaluation/custom_suite.py --pipeline-output  # also write pipeline_output.csv next to the dataset
To try your own scenario: edit custom_cases.py / custom_cases_extra.py (each case = users, activity rows, evidence file, expectation).
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import custom_cases  # noqa: E402
from ghost.engine import Auditor, World  # noqa: E402
from ghost.scoring import FIELDS, score  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=None)
    ap.add_argument('--pipeline-output', action='store_true')
    a = ap.parse_args()
    if a.out:
        os.makedirs(a.out, exist_ok=True)
    d, requests, expected = custom_cases.build(os.path.abspath(a.out) if a.out else None)
    cases = Auditor(World(d)).run(requests)
    pred = {c.case_id: (c.status, [{k: s[k] for k in FIELDS} for s in c.steps]) for c in cases}
    bad = 0
    for cid in expected:
        ok = pred[cid] == expected[cid]
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {cid:10s} {pred[cid][0]:15s} steps={len(pred[cid][1])}")
        if not ok:
            print('   expected:', expected[cid][0], [(s['phase'][:4], s['resource_id'], s['operation']) for s in expected[cid][1]])
            print('   got     :', pred[cid][0], [(s['phase'][:4], s['resource_id'], s['operation']) for s in pred[cid][1]])
    m = score(pred, expected)
    print(f"\n{len(expected) - bad}/{len(expected)} exact | status {m['status_accuracy']:.1%} | step F1 {m['step_f1']:.1%} | "
          f"sequence {m['sequence_exact']:.1%} | evidence_id {m['field_evidence_id']:.1%}")
    if a.out:
        with open(os.path.join(d, 'expected_output.csv'), 'w', encoding='utf-8', newline='') as fh:
            w = csv.writer(fh, lineterminator='\n')
            w.writerow(['case_id', 'deletion_status', 'steps_json'])
            for cid, (st, steps) in expected.items():
                w.writerow([cid, st, json.dumps(steps, separators=(',', ':'))])
        print('dataset + expected_output.csv written to', d)
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
