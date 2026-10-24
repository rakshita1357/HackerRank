"""Scoring helpers used by evaluation/main.py (status, step, field and sequence accuracy)."""
from __future__ import annotations

import csv
import json
from typing import Dict, List, Tuple

FIELDS = ('step_no', 'phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id')


def load_output(path: str) -> Dict[str, Tuple[str, List[dict]]]:
    out = {}
    with open(path, encoding='utf-8', newline='') as fh:
        for r in csv.DictReader(fh):
            out[r['case_id']] = (r['deletion_status'], json.loads(r['steps_json'] or '[]'))
    return out


def _key(s: dict) -> Tuple:
    return (s['phase'], s['system_id'], s['resource_id'], s['operation'])


def _lcs(a: List, b: List) -> int:
    dp = [0] * (len(b) + 1)
    for x in a:
        prev = 0
        for j, y in enumerate(b, 1):
            cur = dp[j]
            dp[j] = prev + 1 if x == y else max(dp[j], dp[j - 1])
            prev = cur
    return dp[-1]


def score(pred: Dict[str, Tuple[str, List[dict]]], gold: Dict[str, Tuple[str, List[dict]]]) -> Dict[str, float]:
    n = len(gold)
    status_ok = 0
    tp = fp = fn = 0
    f_ok = f_tot = 0
    seq_exact = 0
    lcs_sum = 0.0
    full_row = 0
    per_field = {f: [0, 0] for f in FIELDS}
    for cid, (gs, gsteps) in gold.items():
        ps, psteps = pred.get(cid, ('', []))
        status_ok += ps == gs
        gk = {_key(s): s for s in gsteps}
        pk = {_key(s): s for s in psteps}
        both = set(gk) & set(pk)
        tp += len(both)
        fp += len(set(pk) - set(gk))
        fn += len(set(gk) - set(pk))
        for k in gk:
            for f in FIELDS:
                per_field[f][1] += 1
                if k in pk and pk[k].get(f) == gk[k].get(f):
                    per_field[f][0] += 1
        gseq = [_key(s) for s in gsteps]
        pseq = [_key(s) for s in psteps]
        seq_exact += gseq == pseq
        denom = max(len(gseq), len(pseq))
        lcs_sum += 1.0 if denom == 0 else _lcs(gseq, pseq) / denom
        full_row += (ps == gs and gsteps == psteps)
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    out = {'cases': n, 'status_accuracy': status_ok / n, 'step_precision': prec, 'step_recall': rec,
           'step_f1': 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
           'sequence_exact': seq_exact / n, 'sequence_lcs': lcs_sum / n, 'row_exact': full_row / n}
    for f, (a, b) in per_field.items():
        out['field_' + f] = a / b if b else 1.0
    return out
