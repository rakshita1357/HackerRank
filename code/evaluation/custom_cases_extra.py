"""Edge-case suite (cases 6-16) appended to the base custom dataset by custom_cases.build()."""
from __future__ import annotations

import csv
import json
import os
from typing import Dict, List, Tuple

from custom_cases import sha, step

V, F, R = 'VERIFICATION', 'FINDING', 'REMEDIATION'


def _append(path: str, rows: List[Dict[str, str]], fields: List[str]) -> None:
    with open(path, 'a', encoding='utf-8', newline='') as fh:
        csv.DictWriter(fh, fieldnames=fields, lineterminator='\n').writerows(rows)


def _trace(path: str, lines: List[str]) -> None:
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')


def extend(d: str) -> Tuple[List[Dict[str, str]], Dict[str, Tuple[str, List[dict]]]]:
    ev = os.path.join(d, 'evidence')
    users, audit, act, manifest, reqs = [], [], [], [], []
    exp: Dict[str, Tuple[str, List[dict]]] = {}

    def user(n, name, email, phone):
        users.append({'user_id': f'user_{n}', 'name': name, 'email': email, 'phone': phone, 'email_hash': sha(email),
                      'updated_at': '2026-01-01T00:00:00Z'})

    def delete_row(i, t, acc, res='SUCCESS'):
        act.append({'activity_id': f'activity_x{i}', 'event_time': t, 'event_type': 'DELETE_RECORD',
                    'source_system': 'deletion_worker', 'target_system': 'account_service', 'resource_id': acc,
                    'reported_result': res, 'details': f'Deletion worker submitted primary profile {acc} to account_service.'})

    def request(cid, email, req_at, status, rep_at, aud_at, text):
        reqs.append({'case_id': cid, 'email': email, 'requested_at': req_at, 'reported_status': status,
                     'reported_at': rep_at, 'audit_at': aud_at, 'request_text': text})

    def reg(fid, rel, cap):
        manifest.append({'file_id': fid, 'file_path': rel, 'origin': 'custom_edge', 'captured_at': cap})

    def tl(ts, svc, tid, ev_, **kv):
        extra = ' '.join(f'{k}={v}' for k, v in kv.items())
        return f'{ts} level=INFO service={svc} trace_id={tid} event={ev_} {extra}'.strip()

    # --- 6: upper-case / padded e-mail in request and evidence; search leftover; account proven absent by 404 ---------
    user(9006, 'Frank Lee', 'frank.lee@example.test', '+1 555 010 0006')
    request('custom_6', '  Frank.Lee@Example.Test ', '2026-07-01T09:00:00Z', 'COMPLETE', '2026-07-03T09:00:00Z', '2026-07-05T12:00:00Z',
            'Please delete my account and all personal data.')
    _trace(ev + '/logs/resource_lookup_trace_x6.log', [
        tl('2026-07-05T11:00:00Z', 'account_service', 'rb-1', 'profile_lookup_started', resource_id='account_9601', lookup_key='FRANK.LEE@EXAMPLE.TEST'),
        tl('2026-07-05T11:00:00Z', 'account_service', 'rb-1', 'profile_lookup_completed', resource_id='account_9601', rows=0, http_status=404),
        tl('2026-07-05T11:05:00Z', 'search_service', 'rb-2', 'index_query_started', resource_id='search_9602', lookup_key='FRANK.LEE@EXAMPLE.TEST'),
        tl('2026-07-05T11:05:00Z', 'search_service', 'rb-2', 'index_query_completed', resource_id='search_9602', result='hit', total_hits=1,
           index='customer_directory_v3', email='FRANK.LEE@EXAMPLE.TEST', customer_reference='null')])
    reg('evidence_x6', 'evidence/logs/resource_lookup_trace_x6.log', '2026-07-05T11:30:00Z')
    exp['custom_6'] = ('INCOMPLETE', [
        step(1, F, 'search_service', 'search_9602', 'REPORT_REMAINING_RECORD', 'PRESENT', 'evidence_x6'),
        step(2, R, 'search_service', 'search_9602', 'DELETE_RECORD', 'ABSENT'),
        step(3, V, 'account_service', 'account_9601', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
        step(4, V, 'search_service', 'search_9602', 'VERIFY_RECORD_ABSENT', 'ABSENT')])

    # --- 7: only the OLD phone number identifies the analytics row ---------------------------------------------------
    user(9007, 'Gia Hart', 'gia.hart@example.test', '+1 555 010 0107')
    audit.append({'audit_id': 'audit_x7', 'user_id': 'user_9007', 'event_time': '2026-03-01T10:00:00Z', 'field_name': 'phone',
                  'old_value': '+1 555 010 0007', 'new_value': '+1 555 010 0107'})
    request('custom_7', 'gia.hart@example.test', '2026-07-08T09:00:00Z', 'NOT_STARTED', '2026-07-10T09:00:00Z', '2026-07-12T12:00:00Z',
            'Please erase my account. Check both my current and my previous contact details.')
    delete_row(7, '2026-07-10T08:30:00Z', 'account_9702', 'FAILED')
    _trace(ev + '/logs/resource_lookup_trace_x7.log', [
        tl('2026-07-12T11:00:00Z', 'analytics_platform', 'rb-1', 'analytics_profile_read_started', resource_id='analytics_9701', lookup_key='null'),
        tl('2026-07-12T11:00:00Z', 'analytics_platform', 'rb-1', 'analytics_profile_read_completed', resource_id='analytics_9701',
           customer_reference='null', email='null', phone='"+1 555 010 0007"', email_hash='null', audience_cohort='general_shopper')])
    reg('evidence_x7', 'evidence/logs/resource_lookup_trace_x7.log', '2026-07-12T11:30:00Z')
    exp['custom_7'] = ('INCOMPLETE', [
        step(1, F, 'analytics_platform', 'analytics_9701', 'REPORT_EXPOSED_PII', 'PRESENT', 'evidence_x7'),
        step(2, R, 'analytics_platform', 'analytics_9701', 'ANONYMIZE_RECORD', 'ANONYMIZED'),
        step(3, V, 'account_service', 'account_9702', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
        step(4, V, 'analytics_platform', 'analytics_9701', 'VERIFY_RECORD_ANONYMIZED', 'ANONYMIZED')])

    # --- 8: tombstone still carries name / e-mail (must keep only user_id) --------------------------------------------
    user(9008, 'Hal Ito', 'hal.ito@example.test', '+1 555 010 0008')
    request('custom_8', 'hal.ito@example.test', '2026-07-14T09:00:00Z', 'PARTIALLY_COMPLETE', '2026-07-16T09:00:00Z', '2026-07-18T12:00:00Z',
            'I want my account closed and my personal data erased.')
    delete_row(8, '2026-07-16T08:20:00Z', 'account_9802', 'PARTIAL')
    bundle = {'exported_at': '2026-07-18T11:30:00Z', 'source': 'custom', 'snapshots': [
        {'captured_at': '2026-07-18T11:30:00Z', 'data_store': 'identity_service', 'collection': 'deleted_customer_references',
         'records': [{'resource_id': 'tombstone_9801', 'subject_reference': 'user_9008', 'name': 'Hal Ito', 'email': 'hal.ito@example.test',
                      'phone': None, 'email_hash': None, 'account_status': 'DELETED'}]},
        {'captured_at': '2026-07-18T11:30:00Z', 'data_store': 'security_service', 'collection': 'security_audit_events',
         'records': [{'resource_id': 'audit_record_9803', 'subject_reference': 'user_9008', 'event_name': 'account_deletion_reviewed',
                      'actor_type': 'automated_privacy_workflow', 'integrity_seal': 'sha256:00'}]}]}
    json.dump(bundle, open(ev + '/records/customer_data_snapshot_bundle_x8.json', 'w'))
    reg('evidence_x8', 'evidence/records/customer_data_snapshot_bundle_x8.json', '2026-07-18T11:30:00Z')
    exp['custom_8'] = ('INCOMPLETE', [
        step(1, F, 'identity_service', 'tombstone_9801', 'REPORT_EXPOSED_PII', 'PRESENT', 'evidence_x8'),
        step(2, R, 'identity_service', 'tombstone_9801', 'REDACT_RECORD', 'REDACTED'),
        step(3, V, 'account_service', 'account_9802', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
        step(4, V, 'security_service', 'audit_record_9803', 'VERIFY_RECORD_PRESENT', 'PRESENT'),
        step(5, V, 'identity_service', 'tombstone_9801', 'VERIFY_RECORD_REDACTED', 'REDACTED')])

    # --- 9: attachment that belongs only to the subject (delete) + legal hold (retain) -----------------------------------
    user(9009, 'Ivy Cole', 'ivy.cole@example.test', '+1 555 010 0009')
    request('custom_9', 'ivy.cole@example.test', '2026-07-20T09:00:00Z', 'COMPLETE', '2026-07-22T09:00:00Z', '2026-07-24T12:00:00Z',
            'Delete everything you hold about me, except what you are legally required to keep.')
    delete_row(9, '2026-07-22T08:10:00Z', 'account_9902')
    bundle = {'exported_at': '2026-07-24T11:30:00Z', 'source': 'custom', 'snapshots': [
        {'captured_at': '2026-07-24T11:30:00Z', 'data_store': 'support_platform', 'collection': 'ticket_attachments',
         'records': [{'resource_id': 'support_9901', 'ticket_id': 'CS-1', 'file_name': 'invoice.pdf',
                      'uploaded_by': 'ivy.cole@example.test', 'storage_class': 'ticket_attachment'}]},
        {'captured_at': '2026-07-24T11:30:00Z', 'data_store': 'legal_service', 'collection': 'retained_order_records',
         'records': [{'resource_id': 'legal_hold_9903', 'subject_reference': 'user_9009', 'order_reference': 'ORD-1',
                      'retention_reason': 'payment_dispute_and_tax_record', 'retained_until': '2033-12-31T23:59:59Z'}]}]}
    json.dump(bundle, open(ev + '/records/customer_data_snapshot_bundle_x9.json', 'w'))
    reg('evidence_x9', 'evidence/records/customer_data_snapshot_bundle_x9.json', '2026-07-24T11:30:00Z')
    exp['custom_9'] = ('INCOMPLETE', [
        step(1, F, 'support_platform', 'support_9901', 'REPORT_REMAINING_RECORD', 'PRESENT', 'evidence_x9'),
        step(2, R, 'support_platform', 'support_9901', 'DELETE_RECORD', 'ABSENT'),
        step(3, V, 'account_service', 'account_9902', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
        step(4, V, 'support_platform', 'support_9901', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
        step(5, V, 'legal_service', 'legal_hold_9903', 'VERIFY_RECORD_PRESENT', 'PRESENT')])

    # --- 10: workflow said NOT_STARTED but everything is actually compliant (connector off, restore blocked) -----------
    user(9010, 'Jo Park', 'jo.park@example.test', '+1 555 010 0010')
    request('custom_10', 'jo.park@example.test', '2026-07-26T09:00:00Z', 'NOT_STARTED', '2026-07-28T09:00:00Z', '2026-07-30T12:00:00Z',
            'Please remove my account and personal information.')
    _trace(ev + '/logs/resource_lookup_trace_x10.log', [
        tl('2026-07-30T11:00:00Z', 'account_service', 'rb-1', 'profile_lookup_started', resource_id='account_10001', lookup_key='jo.park@example.test'),
        tl('2026-07-30T11:00:00Z', 'account_service', 'rb-1', 'profile_lookup_completed', resource_id='account_10001', rows=0, http_status=404),
        tl('2026-07-30T11:02:00Z', 'integration_service', 'rb-2', 'connector_config_read_started', resource_id='data_flow_10002', lookup_key='user_9010'),
        tl('2026-07-30T11:02:00Z', 'integration_service', 'rb-2', 'connector_config_read_completed', resource_id='data_flow_10002', enabled='false',
           last_delivery_status='stopped', connector_name='support_profile_sync', customer_email='null', customer_reference='user_9010'),
        tl('2026-07-30T11:04:00Z', 'backup_service', 'rb-3', 'restore_policy_read_started', resource_id='backup_10003', lookup_key='user_9010'),
        tl('2026-07-30T11:04:00Z', 'backup_service', 'rb-3', 'restore_policy_read_completed', resource_id='backup_10003', archive_present='true',
           restore_allowed='false', archive_key='archive/x.tar.zst', customer_email='null', customer_reference='user_9010'),
        tl('2026-07-30T11:06:00Z', 'security_service', 'rb-4', 'audit_record_read_started', resource_id='audit_record_10004', lookup_key='user_9010'),
        tl('2026-07-30T11:06:00Z', 'security_service', 'rb-4', 'audit_record_read_completed', resource_id='audit_record_10004', rows=1,
           subject_reference='user_9010', event_code='ACCOUNT_DELETION_RECORDED', retention_until='2031-12-31T23:59:59Z')])
    reg('evidence_x10', 'evidence/logs/resource_lookup_trace_x10.log', '2026-07-30T11:30:00Z')
    e = 'evidence_x10'
    exp['custom_10'] = ('COMPLETE', [
        step(1, V, 'account_service', 'account_10001', 'VERIFY_RECORD_ABSENT', 'ABSENT', e),
        step(2, V, 'security_service', 'audit_record_10004', 'VERIFY_RECORD_PRESENT', 'PRESENT', e),
        step(3, V, 'integration_service', 'data_flow_10002', 'VERIFY_DATA_FLOW_DISABLED', 'DISABLED', e),
        step(4, V, 'backup_service', 'backup_10003', 'VERIFY_RESTORE_BLOCKED', 'BLOCKED', e)])

    # --- 11: deletion words inside a refusal -> not applicable --------------------------------------------------------
    user(9011, 'Kai Wu', 'kai.wu@example.test', '+1 555 010 0011')
    request('custom_11', 'kai.wu@example.test', '2026-08-01T09:00:00Z', 'NOT_STARTED', '2026-08-02T09:00:00Z', '2026-08-04T12:00:00Z',
            'Please do not delete or close my account. I only want to update my phone number.')
    exp['custom_11'] = ('NOT_APPLICABLE', [])

    # --- 12: Korean deletion request with a leftover search entry ------------------------------------------------------
    user(9012, 'Kim Min', 'kim.min@example.test', '+82 10 0000 0012')
    request('custom_12', 'kim.min@example.test', '2026-08-05T09:00:00Z', 'COMPLETE', '2026-08-07T09:00:00Z', '2026-08-09T12:00:00Z',
            '이 이메일 주소에 연결된 계정과 개인정보의 영구 삭제를 요청합니다.')
    _trace(ev + '/logs/resource_lookup_trace_x12.log', [
        tl('2026-08-09T11:00:00Z', 'account_service', 'rb-1', 'profile_lookup_started', resource_id='account_12001', lookup_key='kim.min@example.test'),
        tl('2026-08-09T11:00:00Z', 'account_service', 'rb-1', 'profile_lookup_completed', resource_id='account_12001', rows=0, http_status=404),
        tl('2026-08-09T11:05:00Z', 'search_service', 'rb-2', 'index_query_started', resource_id='search_12002', lookup_key='kim.min@example.test'),
        tl('2026-08-09T11:05:00Z', 'search_service', 'rb-2', 'index_query_completed', resource_id='search_12002', result='hit', total_hits=1,
           index='customer_directory_v3', email='kim.min@example.test', customer_reference='null')])
    reg('evidence_x12', 'evidence/logs/resource_lookup_trace_x12.log', '2026-08-09T11:30:00Z')
    exp['custom_12'] = ('INCOMPLETE', [
        step(1, F, 'search_service', 'search_12002', 'REPORT_REMAINING_RECORD', 'PRESENT', 'evidence_x12'),
        step(2, R, 'search_service', 'search_12002', 'DELETE_RECORD', 'ABSENT'),
        step(3, V, 'account_service', 'account_12001', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
        step(4, V, 'search_service', 'search_12002', 'VERIFY_RECORD_ABSENT', 'ABSENT')])

    # --- 13: record present in an early snapshot, gone from a later exhaustive snapshot of the same batch ---------------
    user(9013, 'Lia Fox', 'lia.fox@example.test', '+1 555 010 0013')
    request('custom_13', 'lia.fox@example.test', '2026-08-10T09:00:00Z', 'PARTIALLY_COMPLETE', '2026-08-12T09:00:00Z', '2026-08-16T12:00:00Z',
            'Please erase my account and all copies.')

    def inv(path, cap, rows):
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write("""CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT, full_name TEXT, email TEXT, phone TEXT, email_hash TEXT, account_status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_search_documents (resource_id TEXT PRIMARY KEY, customer_reference TEXT, display_name TEXT, email TEXT, phone TEXT, email_hash TEXT, index_name TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE security_audit_records (resource_id TEXT PRIMARY KEY, subject_reference TEXT NOT NULL, event_code TEXT NOT NULL, retention_until TEXT NOT NULL, updated_at TEXT NOT NULL);
""")
            for s in ('account_service', 'search_service', 'security_service'):
                fh.write(f"INSERT INTO snapshot_scope VALUES ('{s}', '{cap}');\n")
            fh.write('\n'.join(rows) + '\n')
    inv(ev + '/databases/customer_data_inventory_x13a.sql', '2026-08-13T10:00:00Z', [
        "INSERT INTO customer_profiles VALUES ('account_13001', NULL, NULL, 'lia.fox@example.test', NULL, NULL, 'DELETION_PENDING', '2026-08-13T09:00:00Z');",
        "INSERT INTO customer_search_documents VALUES ('search_13002', NULL, NULL, 'lia.fox@example.test', NULL, NULL, 'customer_directory_v3', '2026-08-13T09:00:00Z');"])
    inv(ev + '/databases/customer_data_inventory_x13b.sql', '2026-08-16T11:00:00Z', [
        "INSERT INTO security_audit_records VALUES ('audit_record_13003', 'user_9013', 'ACCOUNT_DELETION_RECORDED', '2031-12-31T23:59:59Z', '2026-08-16T10:00:00Z');"])
    reg('evidence_x13a', 'evidence/databases/customer_data_inventory_x13a.sql', '2026-08-13T10:00:00Z')
    reg('evidence_x13b', 'evidence/databases/customer_data_inventory_x13b.sql', '2026-08-16T11:00:00Z')
    exp['custom_13'] = ('COMPLETE', [
        step(1, V, 'account_service', 'account_13001', 'VERIFY_RECORD_ABSENT', 'ABSENT', 'evidence_x13b'),
        step(2, V, 'search_service', 'search_13002', 'VERIFY_RECORD_ABSENT', 'ABSENT', 'evidence_x13b'),
        step(3, V, 'security_service', 'audit_record_13003', 'VERIFY_RECORD_PRESENT', 'PRESENT', 'evidence_x13b')])

    # --- 14: only a look-alike address exists in the evidence -> nothing of the subject remains -------------------------
    user(9014, 'Max Ng', 'max.ng@example.test', '+1 555 010 0014')
    request('custom_14', 'max.ng@example.test', '2026-08-18T09:00:00Z', 'COMPLETE', '2026-08-20T09:00:00Z', '2026-08-22T12:00:00Z',
            'Please delete my account permanently.')
    _trace(ev + '/logs/resource_lookup_trace_x14.log', [
        tl('2026-08-22T11:00:00Z', 'account_service', 'rb-1', 'profile_lookup_started', resource_id='account_14001', lookup_key='max.ng@example.test'),
        tl('2026-08-22T11:00:00Z', 'account_service', 'rb-1', 'profile_lookup_completed', resource_id='account_14001', rows=0, http_status=404),
        tl('2026-08-22T11:05:00Z', 'search_service', 'rb-2', 'index_query_started', resource_id='search_14002', lookup_key='max.ng.jr@example.test'),
        tl('2026-08-22T11:05:00Z', 'search_service', 'rb-2', 'index_query_completed', resource_id='search_14002', result='hit', total_hits=1,
           index='customer_directory_v3', email='max.ng.jr@example.test', customer_reference='null')])
    reg('evidence_x14', 'evidence/logs/resource_lookup_trace_x14.log', '2026-08-22T11:30:00Z')
    exp['custom_14'] = ('COMPLETE', [step(1, V, 'account_service', 'account_14001', 'VERIFY_RECORD_ABSENT', 'ABSENT', 'evidence_x14')])

    # --- 15: boundary - evidence captured exactly at audit_at counts, one second later does not ------------------------
    user(9015, 'Noa Roy', 'noa.roy@example.test', '+1 555 010 0015')
    request('custom_15', 'noa.roy@example.test', '2026-08-24T09:00:00Z', 'COMPLETE', '2026-08-26T09:00:00Z', '2026-08-28T12:00:00Z',
            'Please delete my account.')
    _trace(ev + '/logs/resource_lookup_trace_x15a.log', [
        tl('2026-08-28T12:00:00Z', 'account_service', 'rb-1', 'profile_lookup_started', resource_id='account_15001', lookup_key='noa.roy@example.test'),
        tl('2026-08-28T12:00:00Z', 'account_service', 'rb-1', 'profile_lookup_completed', resource_id='account_15001', rows=0, http_status=404)])
    _trace(ev + '/logs/resource_lookup_trace_x15b.log', [
        tl('2026-08-28T12:00:01Z', 'search_service', 'rb-2', 'index_query_started', resource_id='search_15002', lookup_key='noa.roy@example.test'),
        tl('2026-08-28T12:00:01Z', 'search_service', 'rb-2', 'index_query_completed', resource_id='search_15002', result='hit', total_hits=1,
           index='customer_directory_v3', email='noa.roy@example.test', customer_reference='null')])
    reg('evidence_x15a', 'evidence/logs/resource_lookup_trace_x15a.log', '2026-08-28T12:00:00Z')
    reg('evidence_x15b', 'evidence/logs/resource_lookup_trace_x15b.log', '2026-08-28T12:00:01Z')
    exp['custom_15'] = ('COMPLETE', [step(1, V, 'account_service', 'account_15001', 'VERIFY_RECORD_ABSENT', 'ABSENT', 'evidence_x15a')])

    # --- 16: only the restore path remains -------------------------------------------------------------------------------
    user(9016, 'Oli Sen', 'oli.sen@example.test', '+1 555 010 0016')
    request('custom_16', 'oli.sen@example.test', '2026-09-01T09:00:00Z', 'COMPLETE', '2026-09-03T09:00:00Z', '2026-09-05T12:00:00Z',
            'Please erase my account and personal data.')
    _trace(ev + '/logs/resource_lookup_trace_x16.log', [
        tl('2026-09-05T11:00:00Z', 'account_service', 'rb-1', 'profile_lookup_started', resource_id='account_16001', lookup_key='oli.sen@example.test'),
        tl('2026-09-05T11:00:00Z', 'account_service', 'rb-1', 'profile_lookup_completed', resource_id='account_16001', rows=0, http_status=404),
        tl('2026-09-05T11:04:00Z', 'backup_service', 'rb-3', 'restore_policy_read_started', resource_id='backup_16002', lookup_key='oli.sen@example.test'),
        tl('2026-09-05T11:04:00Z', 'backup_service', 'rb-3', 'restore_policy_read_completed', resource_id='backup_16002', archive_present='true',
           restore_allowed='true', archive_key='archive/y.tar.zst', customer_email='oli.sen@example.test', customer_reference='null'),
        tl('2026-09-05T11:06:00Z', 'security_service', 'rb-4', 'audit_record_read_started', resource_id='audit_record_16003', lookup_key='user_9016'),
        tl('2026-09-05T11:06:00Z', 'security_service', 'rb-4', 'audit_record_read_completed', resource_id='audit_record_16003', rows=1,
           subject_reference='user_9016', event_code='ACCOUNT_DELETION_RECORDED', retention_until='2031-12-31T23:59:59Z')])
    reg('evidence_x16', 'evidence/logs/resource_lookup_trace_x16.log', '2026-09-05T11:30:00Z')
    exp['custom_16'] = ('INCOMPLETE', [
        step(1, F, 'backup_service', 'backup_16002', 'REPORT_RESTORE_RISK', 'ACTIVE', 'evidence_x16'),
        step(2, R, 'backup_service', 'backup_16002', 'BLOCK_RESTORE', 'BLOCKED'),
        step(3, V, 'account_service', 'account_16001', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
        step(4, V, 'security_service', 'audit_record_16003', 'VERIFY_RECORD_PRESENT', 'PRESENT'),
        step(5, V, 'backup_service', 'backup_16002', 'VERIFY_RESTORE_BLOCKED', 'BLOCKED')])

    _append(os.path.join(d, 'user_identities.csv'), users, ['user_id', 'name', 'email', 'phone', 'email_hash', 'updated_at'])
    _append(os.path.join(d, 'user_audit_log.csv'), audit, ['audit_id', 'user_id', 'event_time', 'field_name', 'old_value', 'new_value'])
    _append(os.path.join(d, 'system_activity.csv'), act, ['activity_id', 'event_time', 'event_type', 'source_system', 'target_system',
                                                          'resource_id', 'reported_result', 'details'])
    _append(os.path.join(d, 'evidence_files.csv'), manifest, ['file_id', 'file_path', 'origin', 'captured_at'])
    _append(os.path.join(d, 'deletion_requests.csv'), reqs, ['case_id', 'email', 'requested_at', 'reported_status', 'reported_at',
                                                             'audit_at', 'request_text'])
    return reqs, exp
