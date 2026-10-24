"""Identity resolution: request email -> user, plus current and historical identifiers."""
from __future__ import annotations

import csv
import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


def norm_email(v: str) -> str:
    return (v or '').strip().lower()


def norm_phone(v: str) -> str:
    return re.sub(r'\D', '', v or '')


def norm_name(v: str) -> str:
    return re.sub(r'\s+', ' ', (v or '').strip().lower())


@dataclass
class Subject:
    user_id: str
    emails: Set[str] = field(default_factory=set)
    phones: Set[str] = field(default_factory=set)
    hashes: Set[str] = field(default_factory=set)
    names: Set[str] = field(default_factory=set)
    request_email: str = ''

    def matches(self, ids: Dict[str, str]) -> Optional[str]:
        """Return the identifier slot that ties `ids` to this subject (strongest first)."""
        ref = ids.get('ref')
        if ref and ref == self.user_id:
            return 'ref'
        e = norm_email(ids.get('email', ''))
        if e and e in self.emails:
            return 'email'
        h = (ids.get('hash') or '').strip().lower()
        if h and h in self.hashes:
            return 'hash'
        p = norm_phone(ids.get('phone', ''))
        if p and p in self.phones:
            return 'phone'
        return None


class IdentityIndex:
    def __init__(self, dataset_dir: str):
        self.users: Dict[str, Dict[str, str]] = {}
        with open(os.path.join(dataset_dir, 'user_identities.csv'), encoding='utf-8') as fh:
            for r in csv.DictReader(fh):
                self.users[r['user_id']] = r
        self.audit: Dict[str, List[Dict[str, str]]] = {}
        with open(os.path.join(dataset_dir, 'user_audit_log.csv'), encoding='utf-8') as fh:
            for r in csv.DictReader(fh):
                self.audit.setdefault(r['user_id'], []).append(r)
        self._by_email: Dict[str, Set[str]] = {}
        for uid in self.users:
            for e in self.subject(uid).emails:
                self._by_email.setdefault(e, set()).add(uid)

    def subject(self, user_id: str, as_of: Optional[str] = None) -> Subject:
        u = self.users[user_id]
        s = Subject(user_id)
        s.emails.add(norm_email(u['email']))
        s.phones.add(norm_phone(u['phone']))
        s.hashes.add(u['email_hash'].strip().lower())
        s.names.add(norm_name(u['name']))
        for a in self.audit.get(user_id, []):
            if as_of and a['event_time'] > as_of:
                continue
            for v in (a['old_value'], a['new_value']):
                if a['field_name'] == 'email':
                    s.emails.add(norm_email(v))
                elif a['field_name'] == 'email_hash':
                    s.hashes.add(v.strip().lower())
                elif a['field_name'] == 'phone':
                    s.phones.add(norm_phone(v))
        s.phones.discard('')
        s.hashes |= {hashlib.sha256(e.encode()).hexdigest() for e in s.emails if e}
        return s

    def resolve(self, email: str, as_of: Optional[str] = None) -> Optional[Subject]:
        cands = self._by_email.get(norm_email(email), set())
        if not cands:
            return None
        uid = sorted(cands)[0]
        s = self.subject(uid, as_of)
        s.request_email = email
        return s
