"""Structural validation of the produced output.csv against the submission contract."""
from __future__ import annotations

import csv
import json
from typing import List

PHASE_RANK = {'FINDING': 0, 'REMEDIATION': 1, 'VERIFICATION': 2}
OPS = {
    'FINDING': {'REPORT_REMAINING_RECORD': 'PRESENT', 'REPORT_EXPOSED_PII': 'PRESENT',
               'REPORT_ACTIVE_DATA_FLOW': 'ACTIVE', 'REPORT_RESTORE_RISK': 'ACTIVE'},
    'REMEDIATION': {'DISABLE_DATA_FLOW': 'DISABLED', 'BLOCK_RESTORE': 'BLOCKED', 'DELETE_RECORD': 'ABSENT',
                    'REDACT_RECORD': 'REDACTED', 'ANONYMIZE_RECORD': 'ANONYMIZED'},
    'VERIFICATION': {'VERIFY_RECORD_ABSENT': 'ABSENT', 'VERIFY_RECORD_PRESENT': 'PRESENT',
                     'VERIFY_RECORD_REDACTED': 'REDACTED', 'VERIFY_RECORD_ANONYMIZED': 'ANONYMIZED',
                     'VERIFY_DATA_FLOW_DISABLED': 'DISABLED', 'VERIFY_RESTORE_BLOCKED': 'BLOCKED'},
}
FIELDS = {'step_no', 'phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id'}


def validate(path: str, expected_ids: List[str]) -> List[str]:
    """Return a list of problems (empty = valid)."""
    problems: List[str] = []
    with open(path, encoding='utf-8', newline='') as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames != ['case_id', 'deletion_status', 'steps_json']:
            problems.append(f'bad header {rd.fieldnames}')
        rows = list(rd)
    ids = [r['case_id'] for r in rows]
    if ids != sorted(ids):
        problems.append('rows are not sorted by case_id')
    if sorted(ids) != sorted(expected_ids):
        problems.append(f'case ids differ from the request file ({len(ids)} vs {len(expected_ids)})')
    for r in rows:
        cid = r['case_id']
        try:
            steps = json.loads(r['steps_json'])
        except ValueError:
            problems.append(f'{cid}: steps_json is not valid JSON')
            continue
        st = r['deletion_status']
        if st not in ('COMPLETE', 'INCOMPLETE', 'NOT_APPLICABLE'):
            problems.append(f'{cid}: bad status {st}')
        if st == 'NOT_APPLICABLE' and steps != []:
            problems.append(f'{cid}: NOT_APPLICABLE must have []')
        if [s.get('step_no') for s in steps] != list(range(1, len(steps) + 1)):
            problems.append(f'{cid}: step numbers not consecutive')
        last = -1
        for s in steps:
            if set(s) != FIELDS:
                problems.append(f'{cid}: wrong fields {sorted(s)}')
                continue
            if OPS.get(s['phase'], {}).get(s['operation']) != s['state']:
                problems.append(f"{cid}: illegal phase/operation/state {s['phase']}/{s['operation']}/{s['state']}")
            if PHASE_RANK.get(s['phase'], 9) < last:
                problems.append(f'{cid}: phases out of order')
            last = PHASE_RANK.get(s['phase'], 9)
            need_ev = s['phase'] == 'FINDING' or (st == 'COMPLETE')
            if need_ev and not s['evidence_id']:
                problems.append(f"{cid}: step {s['step_no']} needs an evidence_id")
            if not need_ev and s['evidence_id']:
                problems.append(f"{cid}: step {s['step_no']} must have an empty evidence_id")
        if st == 'COMPLETE' and any(s['phase'] != 'VERIFICATION' for s in steps):
            problems.append(f'{cid}: COMPLETE may only contain verification steps')
    return problems
