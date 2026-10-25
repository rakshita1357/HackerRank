"""Turn every evidence file into normalized `Obs` records.

Each extractor is deterministic; free-form artifacts (PDF / PNG) are handled in
`documents.py`. Nothing here looks at request cases - extraction is per file.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .logparse import parse_log
from .model import Coverage, Obs, PREFIX_INFO, rid_prefix, rid_system

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
_HEX64_IN = re.compile(r'\b[0-9a-fA-F]{64}\b')

# JSON field aliases -> canonical identifier slot
_ALIASES = {
    'ref': ('customer_reference', 'user_id', 'subject_reference', 'subject_ref'),
    'email': ('email', 'owner_email', 'customer_email', 'requester_email', 'uploaded_by'),
    'phone': ('phone', 'owner_phone', 'requester_phone'),
    'name': ('full_name', 'display_name', 'requester_name', 'name'),
    'hash': ('email_hash',),
}


def norm_email(v: Optional[str]) -> str:
    return (v or '').strip().lower()


def _ids_from(rec: Dict[str, Any]) -> Dict[str, str]:
    ids: Dict[str, str] = {}
    for slot, names in _ALIASES.items():
        for n in names:
            v = rec.get(n)
            if isinstance(v, str) and v.strip():
                if slot == 'email' and '@' not in v:
                    continue
                if slot == 'ref' and not v.startswith('user_'):
                    continue
                ids.setdefault(slot, v.strip())
    sel = rec.get('subject_selector')
    if isinstance(sel, str) and sel:
        ids.setdefault('ref' if sel.startswith('user_') else 'email', sel.strip())
    return ids


_ATTR_KEYS = ('account_status', 'enabled', 'restore_allowed', 'retention_until', 'retained_until',
              'message_text', 'message_excerpt', 'download_available', 'last_delivery_status',
              'event_code', 'event_name', 'connector_name', 'index_name', 'object_key',
              'archive_key', 'archive_path', 'file_name', 'storage_class', 'audience_cohort',
              'ticket_id', 'order_reference', 'retention_reason', 'channel', 'mode')


_BOOL_KEYS = ('enabled', 'restore_allowed', 'download_available')


def _attrs_from(rec: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: rec[k] for k in _ATTR_KEYS if k in rec}
    for k in _BOOL_KEYS:
        if k in out and out[k] is not None:
            v = out[k]
            out[k] = (v.strip().lower() in ('1', 'true', 'yes')) if isinstance(v, str) else bool(v)
    return out


def obs_from_record(file_id: str, ots: str, fmt: str, rec: Dict[str, Any],
                    topology: str = 'primary') -> Optional[Obs]:
    rid = rec.get('resource_id')
    if not rid:
        return None
    flat = dict(rec)
    if isinstance(rec.get('document'), dict):            # search docs
        flat.update(rec['document'])
    system = rid_system(rid) or ''
    ids = _ids_from(flat)
    for k in ('message_text', 'message_excerpt'):          # identifiers quoted inside free text still identify the record
        txt = flat.get(k)
        if isinstance(txt, str) and txt:
            m = EMAIL_RE.search(txt)
            if m:
                ids.setdefault('email', m.group(0))
            h = _HEX64_IN.search(txt)
            if h:
                ids.setdefault('hash', h.group(0).lower())
    return Obs(file_id=file_id, rid=rid, system=system, ots=ots, fmt=fmt, result='FOUND',
               ids=ids, attrs=_attrs_from({**rec, **flat}), topology=topology)


# --------------------------------------------------------------------- SQL

_INVENTORY_TABLES = {
    'customer_profiles': 'account_service',
    'customer_search_documents': 'search_service',
    'customer_analytics_profiles': 'analytics_platform',
    'support_records': 'support_platform',
    'customer_exports': 'export_service',
    'connector_subscriptions': 'integration_service',
    'backup_restore_entries': 'backup_service',
    'security_audit_records': 'security_service',
}


def _sql_connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    with open(path, encoding='utf-8') as fh:
        con.executescript(fh.read())
    return con


def _header_value(path: str, label: str) -> str:
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            if line.startswith('-- ' + label):
                return line.split(':', 1)[1].strip() if ':' in line else line.split('=', 1)[1].strip()
    return ''


def extract_sql(path: str, file_id: str, captured: str) -> Tuple[List[Obs], List[Coverage]]:
    con = _sql_connect(path)
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    obs: List[Obs] = []
    cov: List[Coverage] = []
    if 'snapshot_scope' in tables:                       # inventory family
        ts = captured
        counts = {}
        for t in tables:
            if t in _INVENTORY_TABLES:
                counts[_INVENTORY_TABLES[t]] = con.execute(f'select count(*) from {t}').fetchone()[0]
        for r in con.execute('select * from snapshot_scope'):
            cov.append(Coverage(file_id, r['origin_system'], r['captured_at'], nonempty=counts.get(r['origin_system'], 0) > 0))
        for t in tables:
            if t not in _INVENTORY_TABLES:
                continue
            for r in con.execute(f'select * from {t}'):
                rec = dict(r)
                o = obs_from_record(file_id, ts, 'sql_inventory', rec)
                if o:
                    obs.append(o)
        return obs, cov
    ts = _header_value(path, 'Captured at') or captured    # replica family
    for t in tables:
        cols = [c[1] for c in con.execute(f'pragma table_info({t})')]
        topo = t.replace('customer_data_', '').replace('customer_profiles_', '')
        topo = {'primary': 'primary'}.get(topo, topo)
        if 'record_json' in cols:
            systems = set()
            for r in con.execute(f'select * from {t}'):
                rec = json.loads(r['record_json'])
                rec.setdefault('resource_id', r['resource_id'])
                o = obs_from_record(file_id, ts, 'sql_replica', rec, topo)
                if o:
                    o.system = r['origin_system'] or o.system
                    obs.append(o)
                    systems.add(o.system)
            for sname in sorted(systems):
                cov.append(Coverage(file_id, sname, ts, topo))
        else:                                            # account / search specific tables
            n = 0
            for r in con.execute(f'select * from {t}'):
                rec = dict(r)
                o = obs_from_record(file_id, ts, 'sql_replica', rec, topo)
                if o:
                    obs.append(o)
                    n += 1
            sysname = 'account_service' if 'profile' in t else ('search_service' if 'search' in t else '')
            if sysname:
                cov.append(Coverage(file_id, sysname, ts, topo, nonempty=n > 0))
    return obs, cov


# -------------------------------------------------------------------- JSON

def _history_obs(file_id: str, rec: Dict[str, Any], fmt: str) -> Obs:
    subj = rec.get('subject_reference') or rec.get('lookup_value') or ''
    return Obs(file_id=file_id, rid=rec['resource_id'], system=rec.get('system') or rid_system(rec['resource_id']) or '',
               ots=rec.get('recorded_at', ''), fmt=fmt, result=rec.get('result', ''),
               ids=_subject_ids(subj), attrs={'record_type': rec.get('record_type', '')}, key=subj)


_HEX64 = re.compile(r'^[0-9a-fA-F]{64}$')


def _subject_ids(subj: str) -> Dict[str, str]:
    subj = (subj or '').strip()
    if _HEX64.match(subj):
        return {'hash': subj.lower()}
    if '@' in subj:
        return {'email': subj}
    if subj.startswith('user_'):
        return {'ref': subj}
    return {}


def extract_json(path: str, file_id: str, captured: str) -> Tuple[List[Obs], List[Coverage]]:
    with open(path, encoding='utf-8') as fh:
        d = json.load(fh)
    obs: List[Obs] = []
    cov: List[Coverage] = []
    if 'service_exports' in d:
        obs += _extract_service_export(d, file_id)
    elif 'snapshots' in d:
        for s in d['snapshots']:
            ts = s['captured_at']
            if s['collection'] == 'operational_record_history':
                for r in s['records']:
                    obs.append(_history_obs(file_id, r, 'json_history'))
                continue
            cov.append(Coverage(file_id, s['data_store'], ts, nonempty=bool(s['records']), scope=not s['records']))
            for r in s['records']:
                o = obs_from_record(file_id, ts, 'json_bundle:' + s['collection'], r)
                if o:
                    if s['collection'] == 'deleted_customer_references':
                        o.attrs['tombstone'] = True
                    if s['collection'] == 'ticket_attachments':
                        o.attrs['attachment'] = True
                    obs.append(o)
    elif 'records' in d and d['records'] and 'record_type' in d['records'][0]:
        for r in d['records']:
            obs.append(_history_obs(file_id, r, 'json_ledger'))
    elif 'records' in d:                                 # analytics report / export jobs
        ts = d.get('captured_at') or captured
        cov.append(Coverage(file_id, rid_system(d['records'][0]['resource_id']) if d['records'] else '', ts))
        for r in d['records']:
            o = obs_from_record(file_id, ts, 'json_records:' + str(d.get('collection', '')), r)
            if o:
                obs.append(o)
    return obs, cov


def _extract_service_export(d: Dict[str, Any], file_id: str) -> List[Obs]:
    out: List[Obs] = []
    se = d['service_exports']
    for r in se.get('account_service', {}).get('profile_lookups', []):
        prof = r.get('profile') or {}
        o = Obs(file_id, r['resource_id'], 'account_service', r['observed_at'], 'json_export',
                result='FOUND' if r.get('status_code') == 200 else 'NOT_FOUND', ids=_ids_from(prof))
        out.append(o)

    def simple(system: str, section: str):
        for r in se.get(system, {}).get(section, []):
            o = obs_from_record(file_id, r['observed_at'], 'json_export', r)
            if o:
                o.system = system
                out.append(o)

    simple('analytics_platform', 'profiles')
    simple('backup_service', 'restore_entries')
    simple('export_service', 'exports')
    simple('integration_service', 'connectors')
    simple('security_service', 'audit_records')
    simple('support_platform', 'tickets')
    for r in se.get('search_service', {}).get('index_queries', []):
        docs = (r.get('hits') or {}).get('documents') or []
        if docs:
            for dd in docs:
                o = obs_from_record(file_id, r['observed_at'], 'json_export', dd)
                if o:
                    o.system = 'search_service'
                    o.key = r.get('query', '')
                    out.append(o)
        else:
            out.append(Obs(file_id, r['resource_id'], 'search_service', r['observed_at'], 'json_export',
                           result='NOT_FOUND', ids=_subject_ids(r.get('query', '')), key=r.get('query', '')))
    return out


# --------------------------------------------------------------------- TXT

_HANDOVER = re.compile(r'^-\s*(\S+)\s*\|\s*(\w+)\s*\|\s*(\S+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(.*?)\s*$')


def extract_txt(path: str, file_id: str, captured: str) -> Tuple[List[Obs], List[Coverage]]:
    obs: List[Obs] = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            m = _HANDOVER.match(line.strip())
            if m:
                ts, system, rid, rtype, res, subj = m.groups()
                obs.append(Obs(file_id, rid, system, ts, 'txt_handover', res, _subject_ids(subj),
                               {'record_type': rtype}, key=subj))
    return obs, []


# -------------------------------------------------------------------- LOGS

def extract_log(path: str, file_id: str, captured: str) -> Tuple[List[Obs], List[Coverage]]:
    recs = parse_log(path)
    obs: List[Obs] = []
    # record_lookup family: one line = one observation
    for r in recs:
        if r.get('event') == 'record_lookup':
            subj = r.get('subject_reference', '')
            obs.append(Obs(file_id, r['resource_id'], r.get('system', ''), r['ts'], 'log_lookup',
                           r.get('result', ''), _subject_ids(subj), {'record_type': r.get('record_type', '')}, key=subj))
    # trace family: group by trace id; the 'started' line carries the key
    by_trace: Dict[str, List[Dict[str, str]]] = {}
    for r in recs:
        if r.get('event') == 'record_lookup':
            continue
        by_trace.setdefault(r.get('trace_id', '') or r.get('requestId', ''), []).append(r)
    for tid, lines in by_trace.items():
        if not tid:
            continue
        key = ''
        for r in lines:
            key = (r.get('lookup_key') or r.get('match_key') or r.get('subject_key') or r.get('scan_key')
                   or r.get('owner_key') or r.get('subject_ref') or key)
            if key:
                break
        for r in lines:
            o = _trace_event_obs(file_id, r, key)
            if o is not None:
                obs.append(o)
    # support replica deletion marker etc. (no trace id)
    return obs, []


def _nz(v: Optional[str]) -> str:
    return '' if v is None or v in ('null', 'None') else v


def _trace_event_obs(file_id: str, r: Dict[str, str], key: str) -> Optional[Obs]:
    ev = r.get('event', '')
    rid = r.get('resource_id', '')
    if not rid:
        return None
    ts = r['ts']
    ids = {}
    mv = _nz(r.get('matched_value'))
    if mv:                                   # a scan hit: classify the matched value by its shape
        if '@' in mv:
            ids['email'] = mv
        elif _HEX64.match(mv):
            ids['hash'] = mv.lower()
        elif re.fullmatch(r'[+()\d\s\-.]{7,}', mv):
            ids['phone'] = mv
        else:
            ids['name'] = mv
    for slot, names in (('email', ('email', 'customer_email', 'requester_email', 'owner_email')),
                        ('phone', ('phone', 'requester_phone')),
                        ('name', ('requester_name', 'display_name')),
                        ('hash', ('email_hash',)),
                        ('ref', ('customer_reference', 'subject_reference'))):
        for n in names:
            v = _nz(r.get(n))
            if v and (slot != 'email' or '@' in v):
                ids.setdefault(slot, v)
    corr = _nz(r.get('correlation_key'))
    if corr:
        ids.update({k: v for k, v in _subject_ids(corr).items() if k not in ids})
    kids = _subject_ids(key)
    for k, v in kids.items():
        ids.setdefault(k, v)
    attrs: Dict[str, Any] = {'event': ev}
    result = ''
    if ev == 'profile_lookup_completed':
        result = 'FOUND' if r.get('rows', '0') != '0' and r.get('http_status', '200') == '200' else 'NOT_FOUND'
    elif ev == 'index_query_completed':
        result = 'FOUND' if r.get('result') == 'hit' else 'NOT_FOUND'
    elif ev in ('analytics_profile_read_completed', 'analytics_profile_read', 'support_ticket_read_completed',
                'message_read', 'export_metadata_read_completed', 'audit_record_read_completed',
                'contact_lookup_completed'):
        result = 'FOUND'
        if ev == 'audit_record_read_completed':
            attrs['retention_until'] = r.get('retention_until', '')
            attrs['event_code'] = r.get('event_code', '')
        if 'message_text' in r:
            attrs['message_text'] = r['message_text']
    elif ev in ('connector_config_read_completed', 'connector_config_loaded', 'connector_heartbeat'):
        result = 'FOUND'
        attrs['enabled'] = r.get('enabled', '').lower() == 'true'
        if 'last_delivery_status' in r:
            attrs['last_delivery_status'] = r['last_delivery_status']
        if 'scheduler_registered' in r:
            attrs['scheduler_registered'] = r['scheduler_registered']
    elif ev in ('restore_policy_read_completed',):
        result = 'FOUND'
        attrs['restore_allowed'] = r.get('restore_allowed', '').lower() == 'true'
        attrs['archive_present'] = r.get('archive_present', '').lower() == 'true'
    elif ev in ('restore_candidate_evaluated',):
        result = 'FOUND'
        attrs['eligible'] = r.get('eligible', '').lower() == 'true'
        attrs['deletion_block'] = r.get('deletion_block', '')
    elif ev in ('restore_authorization_completed',):
        result = 'FOUND'
        attrs['decision'] = r.get('decision', '')
    elif ev in ('export_download_check_completed',):
        result = 'FOUND'
        attrs['available'] = r.get('available', '').lower() == 'true'
        attrs['file'] = r.get('file', '')
    elif ev in ('analytics_identity_scan', 'message_pii_scan', 'attachment_pii_scan'):
        result = 'FOUND'
        attrs['detector'] = r.get('detector', '')
        attrs['direct_identifiers'] = r.get('direct_identifiers', '')
    elif ev == 'contact_query_completed':
        result = 'FOUND' if r.get('rows', '0') != '0' else 'NOT_FOUND'
        attrs['support_record'] = True          # row in a support replica / contact index, not a message body
    elif ev == 'deletion_marker_check':
        result = 'FOUND'
        attrs['marker_found'] = r.get('marker_found', '')
        attrs['support_record'] = True
    else:
        return None
    if 'enabled' in r and 'enabled' not in attrs:
        attrs['enabled'] = r['enabled'].lower() == 'true'
    if ev == 'export_metadata_read_completed':
        attrs['available'] = r.get('available', '').lower() == 'true'
    return Obs(file_id, rid, r.get('service') if r.get('service', '').endswith(('_service', '_platform')) and
               rid_system(rid) == r.get('service') else (rid_system(rid) or r.get('service', '')),
               ts, 'log_trace', result, ids, attrs, key=key)


EXTRACTORS = {'.sql': extract_sql, '.json': extract_json, '.txt': extract_txt, '.log': extract_log}


def load_manifest(dataset_dir: str) -> List[Dict[str, str]]:
    with open(os.path.join(dataset_dir, 'evidence_files.csv'), encoding='utf-8') as fh:
        return list(csv.DictReader(fh))


def extract_all(dataset_dir: str) -> Tuple[List[Obs], List[Coverage], Dict[str, Dict[str, str]]]:
    manifest = {m['file_id']: m for m in load_manifest(dataset_dir)}
    obs: List[Obs] = []
    cov: List[Coverage] = []
    for fid, m in manifest.items():
        path = os.path.join(dataset_dir, m['file_path'])
        ext = os.path.splitext(path)[1].lower()
        fn = EXTRACTORS.get(ext)
        if fn is None:
            continue
        o, c = fn(path, fid, m['captured_at'])
        obs += o
        cov += c
    return obs, cov, manifest
