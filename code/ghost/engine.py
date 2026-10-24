"""Case reconstruction: link resources to a subject, derive state at audit_at, apply policy, plan steps."""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .classify import is_deletion_request
from .evidence import extract_all
from .identity import IdentityIndex, Subject, norm_email
from .model import Coverage, Obs, PREFIX_INFO, rid_prefix, rid_system

FMT = '%Y-%m-%dT%H:%M:%SZ'


def T(s: str) -> datetime:
    return datetime.strptime(s, FMT)


def case_no(case_id: str) -> int:
    try:
        return int(case_id.split('_')[1])
    except (IndexError, ValueError):
        return 0


# ----------------------------------------------------------------------------- state derivation

PRESENT_RESULTS = {'FOUND', 'IDENTIFIABLE', 'ACTIVE', 'REDACTED', 'ANONYMIZED', 'BLOCKED', 'DISABLED'}


@dataclass
class RState:
    rid: str
    prefix: str
    system: str
    required: str            # final required state: ABSENT / REDACTED / ANONYMIZED / PRESENT / DISABLED / BLOCKED
    violation: Optional[str]  # REMAINING / EXPOSED / ACTIVE_FLOW / RESTORE_RISK / None
    evidence_id: str
    ots: str
    detail: str = ''
    kind: str = ''           # message / attachment / marketing ...


def _has_direct_pii(ids: Dict[str, str]) -> bool:
    return any(ids.get(k) for k in ('email', 'phone', 'name', 'hash'))


def derive_state(rid: str, o: Obs, evidence_id: str) -> RState:
    px = rid_prefix(rid)
    system = rid_system(rid) or o.system
    a = o.attrs
    res = o.result
    ev = a.get('event', '')

    def mk(required: str, violation: Optional[str], detail: str = '', kind: str = '') -> RState:
        return RState(rid, px, system, required, violation, evidence_id, o.ots, detail, kind)

    if px in ('account', 'search', 'export', 'marketing', 'object'):
        present = res in PRESENT_RESULTS
        if px == 'export':
            avail = a.get('available', a.get('download_available', True))
            present = present and avail is not False
        return mk('ABSENT', 'REMAINING' if present else None, res)
    if px == 'support':
        is_att = bool(a.get('attachment')) or a.get('record_type') == 'SUPPORT_ATTACHMENT' or ev == 'attachment_pii_scan'
        if is_att:
            return mk('ABSENT', 'REMAINING' if res in PRESENT_RESULTS else None, res, 'attachment')
        if res == 'NOT_FOUND':
            return mk('REDACTED', None, res, 'message')
        text = str(a.get('message_text', ''))
        exposed = (res == 'IDENTIFIABLE' or _has_direct_pii(o.ids) or '@' in text
                   or ev == 'message_pii_scan')
        if res == 'REDACTED' or text.strip() == '[REDACTED]':
            exposed = False
        return mk('REDACTED', 'EXPOSED' if exposed else None, res, 'message')
    if px == 'analytics':
        if ev == 'contact_lookup_completed' or a.get('marketing'):
            return mk('ABSENT', 'REMAINING' if res in PRESENT_RESULTS else None, res, 'marketing')
        if res == 'ANONYMIZED':
            return mk('ANONYMIZED', None, res)
        if res == 'NOT_FOUND':
            return mk('ANONYMIZED', None, res)
        exposed = res == 'IDENTIFIABLE' or any(o.ids.get(k) for k in ('email', 'phone', 'hash', 'name', 'ref'))
        return mk('ANONYMIZED', 'EXPOSED' if exposed else None, res)
    if px == 'data_flow':
        enabled = a.get('enabled')
        active = (res == 'ACTIVE') or (enabled is True)
        return mk('DISABLED', 'ACTIVE_FLOW' if active else None, res)
    if px == 'backup':
        allowed = a.get('restore_allowed')
        risk = (res == 'ACTIVE') or (allowed is True) or (allowed in (1, '1'))
        if ev == 'restore_candidate_evaluated':
            risk = a.get('eligible') is True
        if ev == 'restore_authorization_completed':
            risk = str(a.get('decision', '')).lower() in ('allow', 'allowed', 'permit', 'granted')
        return mk('BLOCKED', 'RESTORE_RISK' if risk else None, res)
    if px == 'audit_record':
        return mk('PRESENT', None, res)
    if px == 'legal_hold':
        return mk('PRESENT', None, res)
    if px == 'tombstone':
        return mk('REDACTED', 'EXPOSED' if _has_direct_pii(o.ids) else None, res)
    return mk('ABSENT', None, res)


# ----------------------------------------------------------------------------- the world

class World:
    def __init__(self, dataset_dir: str):
        self.dir = dataset_dir
        self.identity = IdentityIndex(dataset_dir)
        self.obs, self.coverage, self.manifest = extract_all(dataset_dir)
        self.by_rid: Dict[str, List[Obs]] = defaultdict(list)
        for o in self.obs:
            self.by_rid[o.rid].append(o)
        with open(os.path.join(dataset_dir, 'system_activity.csv'), encoding='utf-8') as fh:
            self.activity = list(csv.DictReader(fh))
        self.act_by_rid: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        for x in self.activity:
            self.act_by_rid[x['resource_id']].append(x)
        # rid -> users it is directly attributed to (by email / user ref anywhere in the evidence)
        self.owner: Dict[str, Set[str]] = defaultdict(set)
        for o in self.obs:
            e = norm_email(o.ids.get('email', ''))
            if e and e in self.identity._by_email:
                self.owner[o.rid] |= self.identity._by_email[e]
            r = o.ids.get('ref')
            if r and r in self.identity.users:
                self.owner[o.rid].add(r)
        for x in self.activity:
            if x['event_type'] == 'LINK_RESOURCE_IDENTITY':
                import re
                for u in re.findall(r'user_\d+', x['details']):
                    self.owner[x['resource_id']].add(u)

    def citable(self, o: Obs, audit_at: str) -> bool:
        return self.manifest[o.file_id]['captured_at'] <= audit_at


@dataclass
class Case:
    row: Dict[str, str]
    case_id: str
    subject: Optional[Subject] = None
    deletion: bool = True
    why: str = ''
    rids: Dict[str, str] = field(default_factory=dict)      # rid -> how it was linked
    status: str = ''
    states: Dict[str, RState] = field(default_factory=dict)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    debug: List[str] = field(default_factory=list)


OP_FIND = ['REPORT_REMAINING_RECORD', 'REPORT_EXPOSED_PII', 'REPORT_ACTIVE_DATA_FLOW', 'REPORT_RESTORE_RISK']
OP_REME = ['DISABLE_DATA_FLOW', 'BLOCK_RESTORE', 'DELETE_RECORD', 'REDACT_RECORD', 'ANONYMIZE_RECORD']
OP_VERI = ['VERIFY_RECORD_ABSENT', 'VERIFY_RECORD_PRESENT', 'VERIFY_RECORD_REDACTED', 'VERIFY_RECORD_ANONYMIZED',
           'VERIFY_DATA_FLOW_DISABLED', 'VERIFY_RESTORE_BLOCKED']
FIND_OF = {'REMAINING': 'REPORT_REMAINING_RECORD', 'EXPOSED': 'REPORT_EXPOSED_PII',
           'ACTIVE_FLOW': 'REPORT_ACTIVE_DATA_FLOW', 'RESTORE_RISK': 'REPORT_RESTORE_RISK'}
FIND_STATE = {'REPORT_REMAINING_RECORD': 'PRESENT', 'REPORT_EXPOSED_PII': 'PRESENT',
              'REPORT_ACTIVE_DATA_FLOW': 'ACTIVE', 'REPORT_RESTORE_RISK': 'ACTIVE'}
REME_STATE = {'DISABLE_DATA_FLOW': 'DISABLED', 'BLOCK_RESTORE': 'BLOCKED', 'DELETE_RECORD': 'ABSENT',
              'REDACT_RECORD': 'REDACTED', 'ANONYMIZE_RECORD': 'ANONYMIZED'}
VERI_STATE = {'VERIFY_RECORD_ABSENT': 'ABSENT', 'VERIFY_RECORD_PRESENT': 'PRESENT',
              'VERIFY_RECORD_REDACTED': 'REDACTED', 'VERIFY_RECORD_ANONYMIZED': 'ANONYMIZED',
              'VERIFY_DATA_FLOW_DISABLED': 'DISABLED', 'VERIFY_RESTORE_BLOCKED': 'BLOCKED'}
REME_OF = {('ABSENT', 'REMAINING'): 'DELETE_RECORD', ('REDACTED', 'EXPOSED'): 'REDACT_RECORD',
           ('ANONYMIZED', 'EXPOSED'): 'ANONYMIZE_RECORD', ('ABSENT', 'EXPOSED'): 'DELETE_RECORD',
           ('DISABLED', 'ACTIVE_FLOW'): 'DISABLE_DATA_FLOW', ('BLOCKED', 'RESTORE_RISK'): 'BLOCK_RESTORE'}
VERI_OF = {'ABSENT': 'VERIFY_RECORD_ABSENT', 'PRESENT': 'VERIFY_RECORD_PRESENT', 'REDACTED': 'VERIFY_RECORD_REDACTED',
           'ANONYMIZED': 'VERIFY_RECORD_ANONYMIZED', 'DISABLED': 'VERIFY_DATA_FLOW_DISABLED',
           'BLOCKED': 'VERIFY_RESTORE_BLOCKED'}


def _step(phase: str, st: RState, op: str, state: str, evidence: str) -> Dict[str, Any]:
    return {'phase': phase, 'system_id': st.system, 'resource_id': st.rid, 'operation': op,
            'state': state, 'evidence_id': evidence}


def build_steps(states: Iterable[RState], complete_only_verification: bool) -> Tuple[str, List[Dict[str, Any]]]:
    sts = sorted(states, key=lambda s: (s.system, s.rid))
    viol = [s for s in sts if s.violation]
    steps: List[Dict[str, Any]] = []
    if not viol:
        for s in sts:
            steps.append(_step('VERIFICATION', s, VERI_OF[s.required], s.required, s.evidence_id))
        return 'COMPLETE', _order_verification(steps)
    for s in viol:
        op = FIND_OF[s.violation]
        steps.append(_step('FINDING', s, op, FIND_STATE[op], s.evidence_id))
    for s in viol:
        op = REME_OF[(s.required, s.violation)]
        steps.append(_step('REMEDIATION', s, op, REME_STATE[op], ''))
    for s in sts:
        steps.append(_step('VERIFICATION', s, VERI_OF[s.required], s.required, ''))
    return 'INCOMPLETE', _order_all(steps)


def _order_verification(steps):
    return _order_all(steps)


def _order_all(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    phase_rank = {'FINDING': 0, 'REMEDIATION': 1, 'VERIFICATION': 2}
    op_rank = {op: i for i, op in enumerate(OP_FIND + OP_REME + OP_VERI)}
    steps = sorted(steps, key=lambda s: (phase_rank[s['phase']], op_rank[s['operation']], s['system_id'], s['resource_id']))
    for i, s in enumerate(steps, 1):
        s['step_no'] = i
    return steps


# ----------------------------------------------------------------------------- the auditor

LINK_SLACK = timedelta(days=1)
RESULT_OF_STATUS = {'COMPLETE': 'SUCCESS', 'PARTIALLY_COMPLETE': 'PARTIAL', 'NOT_STARTED': 'FAILED'}


class Auditor:
    def __init__(self, world: World, llm=None):
        self.w = world
        self.llm = llm

    # ---- linking -------------------------------------------------------------------------
    def direct_links(self, case: Case) -> None:
        row, s = case.row, case.subject
        lo = (T(row['requested_at']) - LINK_SLACK).strftime(FMT)
        hi = row['audit_at']
        for o in self.w.obs:
            if o.ots < lo or o.ots > hi:
                continue
            how = s.matches(o.ids)
            if how:
                case.rids.setdefault(o.rid, how)

    def state_of(self, case: Case, rid: str) -> Optional[RState]:
        hi = case.row['audit_at']
        cands = [o for o in self.w.by_rid.get(rid, []) if o.ots <= hi]
        if not cands:
            return None
        cit = [o for o in cands if self.w.citable(o, hi)]
        pool = cit or cands
        latest = max(o.ots for o in pool)
        top = [o for o in pool if o.ots == latest]
        # among simultaneous observations (primary + replicas / several events) a violation wins
        sts = []
        for o in top:
            sts.append((derive_state(rid, o, o.file_id if self.w.citable(o, hi) else ''), o))
        sts.sort(key=lambda x: (x[0].violation is None, x[0].evidence_id))
        return sts[0][0]

    # ---- null-identity inference (accounts / anonymized analytics) -------------------------------
    def candidate_activity(self, case: Case, system: str, ev_types: Tuple[str, ...], before_h=3, after_h=1):
        rep = T(case.row['reported_at'])
        out = []
        for x in self.w.activity:
            if x['target_system'] != system or x['event_type'] not in ev_types:
                continue
            t = T(x['event_time'])
            if rep - timedelta(hours=before_h) <= t <= rep + timedelta(hours=after_h):
                out.append(x)
        return out

    def run(self, rows: List[Dict[str, str]]) -> List[Case]:
        cases: List[Case] = []
        for row in rows:
            c = Case(row=row, case_id=row['case_id'])
            c.deletion, c.why = is_deletion_request(row['request_text'], self.llm)
            if c.deletion:
                c.subject = self.w.identity.resolve(row['email'], row['audit_at'])
                if c.subject is None:
                    c.debug.append('unresolved identity')
            cases.append(c)
        live = [c for c in cases if c.deletion and c.subject]
        for c in live:
            self.direct_links(c)
        self.infer_accounts(live)
        self.infer_anonymized_analytics(live)
        for c in cases:
            self.finish(c)
        return cases

    def _by_activity_order(self, rids: List[str]) -> List[str]:
        """Unique rids ordered by when the workflow touched them (generation order)."""
        def key(r: str):
            acts = self.w.act_by_rid.get(r, [])
            return (min((int(x['activity_id'].split('_')[1]) for x in acts), default=10 ** 9), r)
        return sorted(set(rids), key=key)

    def _violations(self, c: Case) -> bool:
        for rid in c.rids:
            st = self.state_of(c, rid)
            if st is not None and st.violation:
                return True
        return False

    def infer_accounts(self, live: List[Case]) -> None:
        need: Dict[str, List[str]] = {}
        taken: Set[str] = set()
        for c in live:
            taken.update(r for r in c.rids if rid_prefix(r) == 'account')
        for c in live:
            if any(rid_prefix(r) == 'account' for r in c.rids):
                continue
            cands = self.candidate_activity(c, 'account_service', ('DELETE_RECORD',))
            want = RESULT_OF_STATUS[c.row['reported_status']]
            good = [x for x in cands if x['reported_result'] == want] or cands
            rids = []
            for x in good:
                r = x['resource_id']
                if r in taken:
                    continue
                own = self.w.owner.get(r, set())
                if own and own != {c.subject.user_id}:
                    continue
                rids.append(r)
            # A fully compliant case must be able to *prove* the account is gone (an observation in a
            # citable file); an incomplete case's already-deleted account is simply not listed.
            hi = c.row['audit_at']
            seen = [r for r in rids if any(o.ots <= hi and self.w.citable(o, hi) for o in self.w.by_rid.get(r, []))]
            unseen = [r for r in rids if r not in seen]
            pref = seen if not self._violations(c) else unseen
            need[c.case_id] = self._by_activity_order(pref or rids)
        by_id = {c.case_id: c for c in live}
        for cid, rid in assign(need, by_id).items():
            by_id[cid].rids[rid] = 'activity'

    def infer_anonymized_analytics(self, live: List[Case]) -> None:
        taken: Set[str] = set()
        for c in live:
            taken.update(r for r in c.rids if rid_prefix(r) == 'analytics')
        need: Dict[str, List[str]] = {}
        for c in live:
            px = {rid_prefix(r) for r in c.rids}
            if 'analytics' in px or 'tombstone' in px or self._violations(c):
                continue
            if 'audit_record' not in px:
                continue
            rids = []
            for x in self.candidate_activity(c, 'analytics_platform', ('ANONYMIZE_RECORD',), 3, 1):
                r = x['resource_id']
                if r in taken or x['reported_result'] != 'SUCCESS':
                    continue
                own = self.w.owner.get(r, set())
                if own and own != {c.subject.user_id}:
                    continue
                obs = [o for o in self.w.by_rid.get(r, []) if o.ots <= c.row['audit_at']]
                if not obs or any(o.ids for o in obs):
                    continue
                rids.append(r)
            need[c.case_id] = self._by_activity_order(rids)
        by_id = {c.case_id: c for c in live}
        for cid, rid in assign(need, by_id).items():
            by_id[cid].rids[rid] = 'activity'

    def finish(self, c: Case) -> None:
        if not c.deletion or c.subject is None:
            c.status, c.steps = 'NOT_APPLICABLE', []
            return
        for rid in c.rids:
            st = self.state_of(c, rid)
            if st is None:
                # linked via later evidence / activity only: no observation yet at audit_at
                px = rid_prefix(rid)
                if px == 'account':
                    st = RState(rid, px, 'account_service', 'ABSENT', None, '', '', 'no observation')
                else:
                    continue
            c.states[rid] = st
        c.status, c.steps = build_steps(c.states.values(), True)


TIE_LOG: List[Any] = []


def assign(need: Dict[str, List[str]], cases: Dict[str, Case]) -> Dict[str, str]:
    """Constraint propagation + deterministic tie-break (case order <-> resource order)."""
    need = {k: list(v) for k, v in need.items()}
    out: Dict[str, str] = {}
    changed = True
    while need and changed:
        changed = False
        claim: Dict[str, List[str]] = defaultdict(list)
        for cid, rids in need.items():
            for r in rids:
                claim[r].append(cid)
        for cid in sorted(need, key=case_no):
            rids = need[cid]
            if len(rids) == 1:
                r = rids[0]
                out[cid] = r
                del need[cid]
                for o in need:
                    if r in need[o]:
                        need[o].remove(r)
                changed = True
                break
        if changed:
            continue
        for r, cids in sorted(claim.items()):
            if len(cids) == 1 and cids[0] in need:
                cid = cids[0]
                out[cid] = r
                del need[cid]
                for o in need:
                    if r in need[o]:
                        need[o].remove(r)
                changed = True
                break
        if changed:
            continue
        # genuine ambiguity: pair cases (ascending) with the smallest remaining candidate
        for cid in sorted(need, key=case_no):
            if not need[cid]:
                del need[cid]
                changed = True
                break
        if changed:
            continue
        cid = sorted(need, key=case_no)[0]
        r = need[cid][0]
        TIE_LOG.append((cid, list(need[cid]), [o for o in need if o != cid and set(need[o]) & set(need[cid])]))
        out[cid] = r
        del need[cid]
        for o in need:
            if r in need[o]:
                need[o].remove(r)
        changed = True
    return out
