"""Hand-built evaluation cases (fictional people) that exercise rules not covered by the 32 public samples.

Each case ships with a tiny private dataset written to a temp directory and the answer a careful auditor
should produce.  `build()` returns (dataset_dir, requests, expected) ready for the real pipeline.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REAL_DATASET = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'dataset')


def sha(e: str) -> str:
    return hashlib.sha256(e.lower().encode()).hexdigest()


def _w(path: str, rows: List[Dict[str, str]], fields: List[str]) -> None:
    with open(path, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator='\n')
        w.writeheader()
        w.writerows(rows)


def step(n, phase, system, rid, op, state, ev=''):
    return {'step_no': n, 'phase': phase, 'system_id': system, 'resource_id': rid, 'operation': op,
            'state': state, 'evidence_id': ev}


def build(tmp: str | None = None) -> Tuple[str, List[Dict[str, str]], Dict[str, Tuple[str, List[dict]]]]:
    d = tmp or tempfile.mkdtemp(prefix='ghost_custom_')
    ev = os.path.join(d, 'evidence')
    for sub in ('databases', 'records', 'logs'):
        os.makedirs(os.path.join(ev, sub), exist_ok=True)
    shutil.copy(os.path.join(REAL_DATASET, 'deletion_rules.csv'), os.path.join(d, 'deletion_rules.csv'))

    users = [
        ('user_9001', 'Alice Quill', 'alice.quill@example.test', '+1 555 010 0001'),
        ('user_9002', 'Bob Reyes', 'bob.reyes@example.test', '+1 555 010 0002'),
        ('user_9003', 'Cara Dunn', 'cara.dunn@example.test', '+1 555 010 0003'),
        ('user_9004', 'Dev Patel', 'dev.patel@example.test', '+1 555 010 0004'),
        ('user_9005', 'Eli Moss', 'eli.moss@example.test', '+1 555 010 0005'),
    ]
    _w(os.path.join(d, 'user_identities.csv'),
       [{'user_id': u, 'name': n, 'email': e, 'phone': p, 'email_hash': sha(e), 'updated_at': '2026-01-01T00:00:00Z'}
        for u, n, e, p in users], ['user_id', 'name', 'email', 'phone', 'email_hash', 'updated_at'])
    # Alice used a different email earlier: the request will arrive under the old address
    _w(os.path.join(d, 'user_audit_log.csv'), [
        {'audit_id': 'audit_c1', 'user_id': 'user_9001', 'event_time': '2026-02-01T10:00:00Z', 'field_name': 'email',
         'old_value': 'a.quill@example.test', 'new_value': 'alice.quill@example.test'},
        {'audit_id': 'audit_c2', 'user_id': 'user_9001', 'event_time': '2026-02-01T10:00:00Z', 'field_name': 'email_hash',
         'old_value': sha('a.quill@example.test'), 'new_value': sha('alice.quill@example.test')},
    ], ['audit_id', 'user_id', 'event_time', 'field_name', 'old_value', 'new_value'])

    act = []
    def a(i, t, et, src, tgt, rid, res, det):
        act.append({'activity_id': f'activity_c{i}', 'event_time': t, 'event_type': et, 'source_system': src,
                    'target_system': tgt, 'resource_id': rid, 'reported_result': res, 'details': det})
    a(1, '2026-06-09T10:00:00Z', 'DELETE_RECORD', 'deletion_worker', 'account_service', 'account_9101', 'SUCCESS',
      'Deletion worker submitted primary profile account_9101 to account_service.')
    a(2, '2026-06-19T10:00:00Z', 'DELETE_RECORD', 'deletion_worker', 'account_service', 'account_9201', 'SUCCESS',
      'Deletion worker submitted primary profile account_9201 to account_service.')
    a(3, '2026-06-24T10:00:00Z', 'DELETE_RECORD', 'deletion_worker', 'account_service', 'account_9301', 'SUCCESS',
      'Deletion worker submitted primary profile account_9301 to account_service.')
    a(4, '2026-06-29T10:00:00Z', 'DELETE_RECORD', 'deletion_worker', 'account_service', 'account_9501', 'SUCCESS',
      'Deletion worker submitted primary profile account_9501 to account_service.')
    a(5, '2026-06-10T09:00:00Z', 'CLASSIFY_REQUEST', 'privacy_intake', 'privacy_case_management', 'account_9401', 'SUCCESS',
      'Privacy intake classified request for account_9401 as non-deletion; no destructive action was authorized.')
    _w(os.path.join(d, 'system_activity.csv'), act, ['activity_id', 'event_time', 'event_type', 'source_system',
                                                     'target_system', 'resource_id', 'reported_result', 'details'])

    manifest = []

    # ---- case X1: request under a historical email; identifiers appear as old email / old hash / phone ----------
    sql = ev + '/databases/customer_data_inventory_c1.sql'
    old, h_old = 'a.quill@example.test', sha('a.quill@example.test')
    with open(sql, 'w', encoding='utf-8') as fh:
        fh.write(f"""CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT, full_name TEXT, email TEXT, phone TEXT, email_hash TEXT, account_status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_search_documents (resource_id TEXT PRIMARY KEY, customer_reference TEXT, display_name TEXT, email TEXT, phone TEXT, email_hash TEXT, index_name TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_analytics_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT, email TEXT, phone TEXT, email_hash TEXT, audience_cohort TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE support_records (resource_id TEXT PRIMARY KEY, customer_reference TEXT, requester_name TEXT, requester_email TEXT, requester_phone TEXT, message_excerpt TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE security_audit_records (resource_id TEXT PRIMARY KEY, subject_reference TEXT NOT NULL, event_code TEXT NOT NULL, retention_until TEXT NOT NULL, updated_at TEXT NOT NULL);
BEGIN TRANSACTION;
INSERT INTO snapshot_scope VALUES ('account_service', '2026-06-10T11:30:00Z');
INSERT INTO snapshot_scope VALUES ('search_service', '2026-06-10T11:30:00Z');
INSERT INTO snapshot_scope VALUES ('support_platform', '2026-06-10T11:30:00Z');
INSERT INTO snapshot_scope VALUES ('analytics_platform', '2026-06-10T11:30:00Z');
INSERT INTO snapshot_scope VALUES ('security_service', '2026-06-10T11:30:00Z');
INSERT INTO customer_search_documents VALUES ('search_9102', NULL, NULL, '{old}', NULL, NULL, 'customer_directory_v3', '2026-06-10T11:00:00Z');
INSERT INTO customer_search_documents VALUES ('search_9190', NULL, NULL, 'a.quill.jr@example.test', NULL, NULL, 'customer_directory_v3', '2026-06-10T11:00:00Z');
INSERT INTO support_records VALUES ('support_9103', NULL, NULL, NULL, NULL, 'hash only', '2026-06-10T11:00:00Z');
INSERT INTO customer_analytics_profiles VALUES ('analytics_9104', NULL, NULL, '+1 555 010 0001', NULL, 'general_shopper', '2026-06-10T11:00:00Z');
INSERT INTO security_audit_records VALUES ('audit_record_9105', 'user_9001', 'ACCOUNT_DELETION_RECORDED', '2031-12-31T23:59:59Z', '2026-06-10T11:00:00Z');
COMMIT;
""")
    # the support record is only linked through the e-mail hash of the OLD address
    with open(sql, encoding='utf-8') as fh:
        txt = fh.read().replace("INSERT INTO support_records VALUES ('support_9103', NULL, NULL, NULL, NULL, 'hash only'",
                                f"INSERT INTO support_records VALUES ('support_9103', NULL, NULL, NULL, NULL, 'hash {h_old}'")
    with open(sql, 'w', encoding='utf-8') as fh:
        fh.write(txt)
    manifest.append(('evidence_c1', 'evidence/databases/customer_data_inventory_c1.sql', 'custom_inventory', '2026-06-10T11:30:00Z'))

    # ---- case X2: replication drift, active connector and restore path -------------------------------------------
    sql2 = ev + '/databases/customer_data_read_replica_c2.sql'
    rec_acc = json.dumps({'resource_id': 'account_9201', 'customer_reference': 'user_9002', 'full_name': 'Bob Reyes',
                          'email': 'bob.reyes@example.test', 'phone': '+1 555 010 0002', 'account_status': 'ACTIVE'})
    rec_flow = json.dumps({'resource_id': 'data_flow_9202', 'connector_name': 'warehouse-sync', 'destination': 'warehouse',
                           'subject_selector': 'bob.reyes@example.test', 'enabled': True, 'mode': 'continuous_sync'})
    rec_bak = json.dumps({'resource_id': 'backup_9203', 'archive_path': 's3://b/seg.tar.zst',
                          'subject_reference': 'user_9002', 'restore_allowed': True, 'encryption': 'AES-256'})
    rec_aud = json.dumps({'resource_id': 'audit_record_9204', 'subject_reference': 'user_9002',
                          'event_name': 'account_deletion_reviewed'})
    with open(sql2, 'w', encoding='utf-8') as fh:
        fh.write("-- Captured at: 2026-06-20T09:00:00Z\n-- Database topology: read_replica\n")
        for t in ('customer_data_primary', 'customer_data_read_replica'):
            fh.write(f"CREATE TABLE {t} (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);\n")
        # primary already purged the account; the replica still serves it (drift)
        for rid, sysn, coll, rec in (('data_flow_9202', 'integration_service', 'connector_configurations', rec_flow),
                                     ('backup_9203', 'backup_service', 'backup_catalog', rec_bak),
                                     ('audit_record_9204', 'security_service', 'security_audit_events', rec_aud)):
            fh.write(f"INSERT INTO customer_data_primary VALUES ('{rid}','{sysn}','{coll}','{rec}','2026-06-20T08:00:00Z');\n")
        for rid, sysn, coll, rec in (('account_9201', 'account_service', 'customer_profiles', rec_acc),
                                     ('data_flow_9202', 'integration_service', 'connector_configurations', rec_flow),
                                     ('backup_9203', 'backup_service', 'backup_catalog', rec_bak),
                                     ('audit_record_9204', 'security_service', 'security_audit_events', rec_aud)):
            fh.write(f"INSERT INTO customer_data_read_replica VALUES ('{rid}','{sysn}','{coll}','{rec}','2026-06-20T08:00:00Z');\n")
    manifest.append(('evidence_c2', 'evidence/databases/customer_data_read_replica_c2.sql', 'custom_replica', '2026-06-20T09:00:00Z'))

    # ---- case X3: fully compliant account with a tombstone (stable user_id only) and a retained audit record -----
    bundle = {'exported_at': '2026-06-25T10:00:00Z', 'source': 'custom', 'snapshots': [
        {'captured_at': '2026-06-25T10:00:00Z', 'data_store': 'account_service', 'collection': 'customer_profiles', 'records': []},
        {'captured_at': '2026-06-25T10:00:00Z', 'data_store': 'identity_service', 'collection': 'deleted_customer_references',
         'records': [{'resource_id': 'tombstone_9302', 'subject_reference': 'user_9003', 'name': None, 'email': None,
                      'phone': None, 'email_hash': None, 'account_status': 'DELETED'}]},
        {'captured_at': '2026-06-25T10:00:00Z', 'data_store': 'security_service', 'collection': 'security_audit_events',
         'records': [{'resource_id': 'audit_record_9303', 'subject_reference': 'user_9003',
                      'event_name': 'account_deletion_reviewed', 'actor_type': 'automated_privacy_workflow',
                      'integrity_seal': 'sha256:00'}]}]}
    with open(ev + '/records/customer_data_snapshot_bundle_c3.json', 'w', encoding='utf-8') as fh:
        json.dump(bundle, fh)
    manifest.append(('evidence_c3', 'evidence/records/customer_data_snapshot_bundle_c3.json', 'custom_bundle', '2026-06-25T10:00:00Z'))

    # ---- case X5: evidence captured AFTER audit_at must not change the answer ----------------------------------------
    with open(ev + '/logs/resource_lookup_trace_c5a.log', 'w', encoding='utf-8') as fh:
        fh.write('2026-06-30T09:00:00Z level=INFO service=readback_collector event=export_started target_count=2 mode=point_in_time\n'
                 '2026-06-30T09:00:01Z level=DEBUG service=account_service trace_id=rb-1 event=profile_lookup_started resource_id=account_9501 lookup_key=eli.moss@example.test\n'
                 '2026-06-30T09:00:01Z level=INFO service=account_service trace_id=rb-1 event=profile_lookup_completed resource_id=account_9501 rows=0 http_status=404\n'
                 '2026-06-30T09:10:00Z level=DEBUG service=security_service trace_id=rb-2 event=audit_record_read_started resource_id=audit_record_9502 lookup_key=user_9005\n'
                 '2026-06-30T09:10:00Z level=INFO service=security_service trace_id=rb-2 event=audit_record_read_completed resource_id=audit_record_9502 rows=1 subject_reference=user_9005 event_code=ACCOUNT_DELETION_RECORDED retention_until=2031-12-31T23:59:59Z\n'
                 '2026-06-30T09:20:00Z level=INFO service=readback_collector event=export_completed queries=2 dropped=0\n')
    with open(ev + '/logs/resource_lookup_trace_c5b.log', 'w', encoding='utf-8') as fh:
        fh.write('2026-07-05T09:00:00Z level=INFO service=readback_collector event=export_started target_count=1 mode=point_in_time\n'
                 '2026-07-05T09:00:01Z level=DEBUG service=search_service trace_id=rb-3 event=index_query_started resource_id=search_9503 lookup_key=eli.moss@example.test\n'
                 '2026-07-05T09:00:01Z level=INFO service=search_service trace_id=rb-3 event=index_query_completed resource_id=search_9503 result=hit total_hits=1 index=customer_directory_v3 email=eli.moss@example.test customer_reference=null\n'
                 '2026-07-05T09:00:02Z level=INFO service=readback_collector event=export_completed queries=1 dropped=0\n')
    manifest.append(('evidence_c5a', 'evidence/logs/resource_lookup_trace_c5a.log', 'custom_trace', '2026-06-30T09:20:00Z'))
    manifest.append(('evidence_c5b', 'evidence/logs/resource_lookup_trace_c5b.log', 'custom_trace', '2026-07-05T09:00:02Z'))

    _w(os.path.join(d, 'evidence_files.csv'),
       [{'file_id': f, 'file_path': p, 'origin': o, 'captured_at': c} for f, p, o, c in manifest],
       ['file_id', 'file_path', 'origin', 'captured_at'])

    req = lambda cid, email, rq, st, rp, au, text: {'case_id': cid, 'email': email, 'requested_at': rq, 'reported_status': st,
                                                    'reported_at': rp, 'audit_at': au, 'request_text': text}
    requests = [
        req('custom_1', 'a.quill@example.test', '2026-06-07T09:00:00Z', 'COMPLETE', '2026-06-09T10:30:00Z', '2026-06-10T12:00:00Z',
            'I used another address before. Please delete my account and every copy linked to either address.'),
        req('custom_2', 'bob.reyes@example.test', '2026-06-17T09:00:00Z', 'COMPLETE', '2026-06-19T10:30:00Z', '2026-06-20T10:00:00Z',
            'Please permanently delete my account.'),
        req('custom_3', 'cara.dunn@example.test', '2026-06-22T09:00:00Z', 'COMPLETE', '2026-06-24T10:30:00Z', '2026-06-25T11:00:00Z',
            'Eliminen mi cuenta y mis datos personales, por favor.'),
        req('custom_4', 'dev.patel@example.test', '2026-06-08T09:00:00Z', 'NOT_STARTED', '2026-06-09T09:00:00Z', '2026-06-10T09:00:00Z',
            'Please unsubscribe me from the marketing newsletter only. I still use my account and want to keep it.'),
        req('custom_5', 'eli.moss@example.test', '2026-06-27T09:00:00Z', 'COMPLETE', '2026-06-29T10:30:00Z', '2026-07-01T12:00:00Z',
            'Please erase my account and personal data.'),
    ]
    _w(os.path.join(d, 'deletion_requests.csv'), requests,
       ['case_id', 'email', 'requested_at', 'reported_status', 'reported_at', 'audit_at', 'request_text'])

    V, F, R = 'VERIFICATION', 'FINDING', 'REMEDIATION'
    exp: Dict[str, Tuple[str, List[dict]]] = {
        'custom_1': ('INCOMPLETE', [
            step(1, F, 'search_service', 'search_9102', 'REPORT_REMAINING_RECORD', 'PRESENT', 'evidence_c1'),
            step(2, F, 'analytics_platform', 'analytics_9104', 'REPORT_EXPOSED_PII', 'PRESENT', 'evidence_c1'),
            step(3, F, 'support_platform', 'support_9103', 'REPORT_EXPOSED_PII', 'PRESENT', 'evidence_c1'),
            step(4, R, 'search_service', 'search_9102', 'DELETE_RECORD', 'ABSENT'),
            step(5, R, 'support_platform', 'support_9103', 'REDACT_RECORD', 'REDACTED'),
            step(6, R, 'analytics_platform', 'analytics_9104', 'ANONYMIZE_RECORD', 'ANONYMIZED'),
            step(7, V, 'account_service', 'account_9101', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
            step(8, V, 'search_service', 'search_9102', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
            step(9, V, 'security_service', 'audit_record_9105', 'VERIFY_RECORD_PRESENT', 'PRESENT'),
            step(10, V, 'support_platform', 'support_9103', 'VERIFY_RECORD_REDACTED', 'REDACTED'),
            step(11, V, 'analytics_platform', 'analytics_9104', 'VERIFY_RECORD_ANONYMIZED', 'ANONYMIZED')]),
        'custom_2': ('INCOMPLETE', [
            step(1, F, 'account_service', 'account_9201', 'REPORT_REMAINING_RECORD', 'PRESENT', 'evidence_c2'),
            step(2, F, 'integration_service', 'data_flow_9202', 'REPORT_ACTIVE_DATA_FLOW', 'ACTIVE', 'evidence_c2'),
            step(3, F, 'backup_service', 'backup_9203', 'REPORT_RESTORE_RISK', 'ACTIVE', 'evidence_c2'),
            step(4, R, 'integration_service', 'data_flow_9202', 'DISABLE_DATA_FLOW', 'DISABLED'),
            step(5, R, 'backup_service', 'backup_9203', 'BLOCK_RESTORE', 'BLOCKED'),
            step(6, R, 'account_service', 'account_9201', 'DELETE_RECORD', 'ABSENT'),
            step(7, V, 'account_service', 'account_9201', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
            step(8, V, 'security_service', 'audit_record_9204', 'VERIFY_RECORD_PRESENT', 'PRESENT'),
            step(9, V, 'integration_service', 'data_flow_9202', 'VERIFY_DATA_FLOW_DISABLED', 'DISABLED'),
            step(10, V, 'backup_service', 'backup_9203', 'VERIFY_RESTORE_BLOCKED', 'BLOCKED')]),
        'custom_3': ('COMPLETE', [
            step(1, V, 'account_service', 'account_9301', 'VERIFY_RECORD_ABSENT', 'ABSENT', 'evidence_c3'),
            step(2, V, 'identity_service', 'tombstone_9302', 'VERIFY_RECORD_REDACTED', 'REDACTED', 'evidence_c3'),
            step(3, V, 'security_service', 'audit_record_9303', 'VERIFY_RECORD_PRESENT', 'PRESENT', 'evidence_c3')]),
        'custom_4': ('NOT_APPLICABLE', []),
        'custom_5': ('COMPLETE', [
            step(1, V, 'account_service', 'account_9501', 'VERIFY_RECORD_ABSENT', 'ABSENT', 'evidence_c5a'),
            step(2, V, 'security_service', 'audit_record_9502', 'VERIFY_RECORD_PRESENT', 'PRESENT', 'evidence_c5a')]),
    }
    # steps in COMPLETE cases are ordered by operation then system: fix numbering of case 3
    exp['custom_3'] = ('COMPLETE', [
        step(1, V, 'account_service', 'account_9301', 'VERIFY_RECORD_ABSENT', 'ABSENT', 'evidence_c3'),
        step(2, V, 'security_service', 'audit_record_9303', 'VERIFY_RECORD_PRESENT', 'PRESENT', 'evidence_c3'),
        step(3, V, 'identity_service', 'tombstone_9302', 'VERIFY_RECORD_REDACTED', 'REDACTED', 'evidence_c3')])
    return d, requests, exp
