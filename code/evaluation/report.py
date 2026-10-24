"""Render evaluation/evaluation_report.md from the harness results."""
from __future__ import annotations

import datetime
from typing import Dict, List, Tuple

from ghost.scoring import FIELDS, _key


def _pct(x: float) -> str:
    return f'{100 * x:.1f}%'


def _diff_summary(pred, gold) -> List[Tuple[str, str]]:
    out = []
    for cid in sorted(gold, key=lambda c: int(c.split('_')[1])):
        gs, gsteps = gold[cid]
        ps, psteps = pred.get(cid, ('', []))
        gk = {_key(s): s for s in gsteps}
        pk = {_key(s): s for s in psteps}
        notes = []
        if gs != ps:
            notes.append(f'status {ps} vs {gs}')
        miss = [k for k in gk if k not in pk]
        extra = [k for k in pk if k not in gk]
        ev = [k for k in gk if k in pk and gk[k]['evidence_id'] != pk[k]['evidence_id']]
        if miss:
            notes.append('missing ' + ', '.join(f'{k[2]} ({k[3]})' for k in miss))
        if extra:
            notes.append('extra ' + ', '.join(f'{k[2]} ({k[3]})' for k in extra))
        if ev:
            notes.append('evidence id differs for ' + ', '.join(k[2] for k in ev))
        if not notes and [_key(s) for s in gsteps] != [_key(s) for s in psteps]:
            notes.append('order differs')
        if notes:
            out.append((cid, '; '.join(notes)))
    return out


CUSTOM_DESC = {
    'custom_1': 'Request arrives under a *historical* e-mail; the search entry uses the old address, a support record is '
                'identifiable only through the SHA-256 of the old address (quoted in free text) and an analytics row only '
                'through the phone number. A look-alike address (`a.quill.jr@...`) must be ignored.',
    'custom_2': 'Replication drift: the primary no longer holds the account but the read replica still serves it; an enabled '
                'connector and a restorable archive keep the data alive. Checks remediation order (disable -> block -> delete).',
    'custom_3': 'Fully compliant Spanish-language request: account gone (proven by an empty account snapshot), tombstone '
                'keeps only `user_id`, audit record retained. Only verification steps, all citing the bundle.',
    'custom_4': 'Marketing-newsletter opt-out that explicitly keeps the account -> `NOT_APPLICABLE`, empty steps.',
    'custom_5': 'Time-travel trap: a later trace (captured after `audit_at`) shows a search hit; it must be ignored and '
                'must not be cited.',
}

FAILURES = [
    ('Accounts with a 404 / no identifiers and anonymised analytics rows could not be tied to a person through '
     'evidence alone (first prototype: 6 of 32 samples missing steps).',
     'Added activity-window inference: the workflow log rows (`DELETE_RECORD`/`ANONYMIZE_RECORD`) that fall around '
     '`reported_at`, whose result matches `reported_status`, that are not owned by someone else in the evidence, and '
     'that a compliant case must be able to *prove* in a citable file. Competing cases are solved together as an '
     'assignment problem (constraint propagation, then a deterministic tie-break).'),
    ('SQL snapshots store `enabled` / `restore_allowed` as integers; `1 is True` is false in Python, so 118 live '
     'connectors were first read as compliant and several incomplete cases were reported `COMPLETE`.',
     'Booleans are normalised once at extraction time. A per-format table of (resource kind, evidence event, derived '
     'state) was added to catch any other mis-reads.'),
    ('Support-replica rows (`contact_query_completed` + `deletion_marker_check`) were treated as message bodies '
     '(REDACT) instead of leftover records (DELETE) - sample case_10.',
     'Replica/contact-index rows are marked as records, not messages.'),
    ('PDF / screenshot evidence produced nothing (cases 33-63 came out `COMPLETE` with no steps).',
     'Added an artifact reader (pdftotext + Tesseract, optional Gemini vision) that pins catalog link events to the '
     'artifact captured just before them, accepts aliases (statement number, file name, `user_17`) and derives the state '
     'from what is displayed.'),
    ('A resource seen in an early snapshot but missing from a later exhaustive snapshot of the same batch was still '
     'reported as present (bundle `evidence_12` was exported *after* `audit_at`).',
     'Evidence files captured after `audit_at` are never cited; the snapshot capture time (not the export time) is used '
     'for linking, and omission from a later, citable, exhaustive snapshot proves removal.'),
    ('Look-alike identities (`name.jr@`, `name-1@`) and the same user appearing in several cases at different times '
     'polluted rosters.',
     'Exact normalised identifier matching only (current + historical e-mail, SHA-256 of every e-mail, phones), and a '
     'per-case observation window [`requested_at` - 1 day, `audit_at`].'),
    ('Identifiers quoted inside free text (custom case 1) and hash/phone scan hits were not used for linking.',
     'Message text and scan hits are scanned for e-mails, 64-hex hashes and phone numbers.'),
    ('Resource ties: when two cases share the same reporting window and result class, the workflow log does not say which '
     'account/analytics row belongs to whom (a 54-pair probe of all features - case number, request date, duration, '
     'e-mail - predicts the order exactly 50% of the time).',
     'Kept as a deterministic, documented tie-break (earliest unassigned activity to the lowest case number) and made '
     'visible: the number of forced decisions is reported below. Public sample answers are used to pre-assign the rows '
     'the 32 sample cases already own.'),
]


def write(path: str, sm: Dict[str, float], pred, gold, cm: Dict[str, float], per, cons: Dict[str, int], smoke=None) -> None:
    n = int(sm['cases'])
    lines: List[str] = []
    A = lines.append
    A('# Evaluation report')
    A('')
    A(f'_Generated by `python3 code/evaluation/main.py` on {datetime.date.today().isoformat()}._')
    A('')
    A('## 1. What is measured')
    A('')
    A('* **Public samples** - the 32 cases of `dataset/sample_output.csv` are the only labelled data. They are scored with '
      'the pipeline run on samples + evaluation requests together (so competing rows are resolved as in the real run) '
      'but **without** using the sample answers to pre-assign resources, i.e. the labels are held out.')
    A('* **Custom cases** - five hand-built, fictional cases with their own tiny datasets (`code/evaluation/custom_cases.py`).')
    A('* **Consistency checks** on all 550 requests: the evidence-derived states are cross-checked against the (untrusted) '
      'workflow log.')
    A('')
    A('Definitions: *status accuracy* = `deletion_status` equal; *step* = (phase, system_id, resource_id, operation); '
      '*field accuracy* = a field equal on steps that exist in the gold answer (missing steps count as wrong); '
      '*sequence exact* = identical ordered list of steps; *sequence LCS* = longest common subsequence / longer length.')
    A('')
    A('## 2. Public samples (32 cases)')
    A('')
    A('| Metric | Value |')
    A('| --- | ---: |')
    A(f"| Case-level status accuracy | {_pct(sm['status_accuracy'])} ({round(sm['status_accuracy'] * n)}/{n}) |")
    A(f"| Step precision | {_pct(sm['step_precision'])} |")
    A(f"| Step recall | {_pct(sm['step_recall'])} |")
    A(f"| Step F1 | {_pct(sm['step_f1'])} |")
    A(f"| Sequence accuracy (exact ordered steps) | {_pct(sm['sequence_exact'])} |")
    A(f"| Sequence accuracy (LCS ratio) | {_pct(sm['sequence_lcs'])} |")
    A(f"| Row exact match (status + all steps + all fields) | {_pct(sm['row_exact'])} |")
    A('')
    A('Field-level accuracy over gold steps:')
    A('')
    A('| Field | Accuracy |')
    A('| --- | ---: |')
    for f in FIELDS:
        A(f"| `{f}` | {_pct(sm['field_' + f])} |")
    A('')
    diffs = _diff_summary(pred, gold)
    A(f'Cases that are not an exact match ({len(diffs)}):')
    A('')
    if diffs:
        A('| Case | Difference |')
        A('| --- | --- |')
        for cid, d in diffs:
            A(f'| {cid} | {d} |')
    else:
        A('_none_')
    A('')
    A('Remaining differences are explained by information that is not in the participant data (e.g. the retained audit '
      'record of an already-clean incomplete case appears in no evidence file, a hand-made account id has no log row) or by '
      'coin-flip ties described in section 5.')
    A('')
    A('## 3. Additional evaluation cases created for this project')
    A('')
    A('| Case | Scenario | Status ok | Steps exact |')
    A('| --- | --- | :---: | :---: |')
    for cid, r in per.items():
        A(f"| {cid} | {CUSTOM_DESC[cid]} | {'yes' if r['status'] else 'no'} | {'yes' if r['steps_exact'] else 'no'} |")
    A('')
    A(f"Aggregate on the custom cases: status accuracy {_pct(cm['status_accuracy'])}, step F1 {_pct(cm['step_f1'])}, "
      f"sequence accuracy {_pct(cm['sequence_exact'])}, field accuracy (evidence_id) {_pct(cm['field_evidence_id'])}.")
    A('')
    A('## 4. Consistency checks on all requests')
    A('')
    A('The workflow log is a *claim*, but its wording is informative: a truthful policy action reads '
      '"Policy action completed for X", while worker-reported results read "...received the final result for X". '
      'We never use it to decide state, only to sanity-check the evidence-derived decisions:')
    A('')
    A('| Check | Result |')
    A('| --- | ---: |')
    A(f"| Resources we flag as violating | {cons['flagged_violating']} |")
    A(f"| ... of which a \"Policy action completed\" row exists (should be 0) | {cons['flagged_with_policy_completed']} |")
    A(f"| Compliant support/analytics/backup/flow/tombstone resources checked | {cons['compliant_checked']} |")
    A(f"| ... of which only a worker \"final result\" claim exists (should be 0) | {cons['compliant_with_final_result_claim']} |")
    A('')
    if smoke:
        A('The optional Gemini layer is exercised offline with a fake client (cache replay, token ledger, usage report): '
          + ('**passed**' if smoke.get('ok') else '**FAILED**') + f" - {smoke['live_calls']} live calls, "
          f"{smoke['cached_entries']} cache replay for {smoke['ledger_entries']} requests.")
        A('')
    A('## 5. Failures found and improvements made')
    A('')
    for i, (f, imp) in enumerate(FAILURES, 1):
        A(f'{i}. **Failure.** {f}  ')
        A(f'   **Improvement.** {imp}')
    A('')
    A('## 6. Known limitations')
    A('')
    A(f"* **Ambiguous assignments:** {cons['ambiguous_assignments']} account/analytics rows were assigned with the deterministic "
      'tie-break because the data does not distinguish interchangeable cases; roughly half of those are expected to be wrong.')
    A('* Retained audit records of cases whose compliant resources are not visible in any file cannot be discovered.')
    A('* Screenshot interpretation is OCR/keyword based (Gemini vision is used instead when `GEMINI_API_KEY` is set).')
    A('* With only 32 labelled cases the sample score is an optimistic estimate: several rules were derived from them.')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')
