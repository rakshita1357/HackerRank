"""Shared data structures and constants for the deletion-audit pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# resource-id prefix -> (owning system, rule data_category)
PREFIX_INFO = {
    'account': ('account_service', 'USER_PROFILE'),
    'search': ('search_service', 'SEARCH_INDEX_ENTRY'),
    'support': ('support_platform', 'SUPPORT_MESSAGE'),
    'analytics': ('analytics_platform', 'ANALYTICS_PROFILE'),
    'export': ('export_service', 'USER_EXPORT'),
    'data_flow': ('integration_service', 'DATA_FLOW'),
    'backup': ('backup_service', 'BACKUP_ARCHIVE'),
    'audit_record': ('security_service', 'SECURITY_AUDIT_LOG'),
    'tombstone': ('identity_service', 'ACCOUNT_TOMBSTONE'),
    'marketing': ('marketing_platform', 'MARKETING_PROFILE'),
    'legal_hold': ('legal_service', 'LEGAL_HOLD_RECORD'),
    'object': ('object_storage', 'SUPPORT_ATTACHMENT'),
    'cache': ('cache_service', 'CACHE_ENTRY'),
    'email_delivery': ('email_delivery_service', 'EMAIL_DELIVERY_LOG'),
    'telemetry': ('telemetry_service', 'PRODUCT_TELEMETRY'),
    'secondary': ('identity_service', 'SECONDARY_IDENTIFIER'),
}


def rid_prefix(rid: str) -> str:
    """'audit_record_12' -> 'audit_record'; 'data_flow_3' -> 'data_flow'."""
    head, _, tail = rid.rpartition('_')
    return head if tail.isdigit() else rid


def rid_system(rid: str) -> Optional[str]:
    info = PREFIX_INFO.get(rid_prefix(rid))
    return info[0] if info else None


@dataclass
class Obs:
    """One observation of one resource, as stated by one evidence file."""
    file_id: str
    rid: str
    system: str
    ots: str                      # observation timestamp (ISO, Z)
    fmt: str                      # extractor name
    result: str = ''              # FOUND / NOT_FOUND / IDENTIFIABLE / ANONYMIZED / ACTIVE ...
    ids: Dict[str, str] = field(default_factory=dict)   # email, phone, name, hash, ref
    attrs: Dict[str, Any] = field(default_factory=dict)
    topology: str = 'primary'
    key: str = ''                 # lookup key / subject as written in the evidence

    @property
    def category(self) -> str:
        info = PREFIX_INFO.get(rid_prefix(self.rid))
        return info[1] if info else ''


@dataclass
class Coverage:
    """A file asserts it enumerated every record of `system` as of `ts`."""
    file_id: str
    system: str
    ts: str
    topology: str = 'primary'
