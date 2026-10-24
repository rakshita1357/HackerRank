You are reading a screenshot or document from an internal Northstar Shop business tool (support console, export
centre, connector manager, backup manager, analytics, search console, object storage, compliance journal, ...).
Extract only facts that are visible. Do not guess and do not assume a deletion verdict.

Return strict JSON with these keys (use null when the artifact does not show the fact):
{
  "resource_ids": ["ids such as support_1034, data_flow_1121, backup_9287, export_1260, search_428, analytics_7438, audit_record_003, marketing_001, tombstone_17, object_19"],
  "emails": ["email addresses displayed"],
  "phones": ["phone numbers displayed"],
  "person_names": ["person names displayed"],
  "shows_direct_identifiers": true|false|null,    // name, e-mail, phone or e-mail hash of a person is displayed for the main record
  "data_flow_active": true|false|null,             // a connector / sync is enabled, healthy or delivering events
  "restore_possible": true|false|null,             // an archive/backup can still be restored (no deletion block)
  "record_present": true|false|null,               // the main record is displayed as existing
  "retention_until": "<ISO date>"|null,
  "notes": "<=25 words"
}
