"""PDF / screenshot evidence: extract text (pdftotext, tesseract) and turn it into observations.

Business artifacts do not state a deletion verdict. We read the facts they contain: which
resource ids and identifiers are displayed, and what the screen/document says about that record.
Resource <-> person links come from `LINK_RESOURCE_IDENTITY` catalog events (a claim we still check
against the artifact itself: the resource id must be visible in the artifact).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from .model import Obs, rid_prefix, rid_system

FMT = '%Y-%m-%dT%H:%M:%SZ'
RID_RE = re.compile(r'\b(account|search|support|analytics|export|data_flow|backup|audit_record|tombstone|marketing|object|legal_hold)_(\d+)\b')
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')

_CACHE: Dict[str, str] = {}


def extract_text(path: str) -> str:
    """Text of a PDF (pdftotext) or PNG (tesseract). Deterministic; cached in-process."""
    if path in _CACHE:
        return _CACHE[path]
    text = ''
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == '.pdf' and shutil.which('pdftotext'):
            text = subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True, text=True, timeout=60).stdout
        elif ext in ('.png', '.jpg', '.jpeg') and shutil.which('tesseract'):
            outs = []
            for psm in ('11', '6'):
                outs.append(subprocess.run(['tesseract', path, '-', '--psm', psm], capture_output=True,
                                           text=True, timeout=120).stdout)
            text = '\n'.join(outs)
    except (OSError, subprocess.SubprocessError):
        text = ''
    _CACHE[path] = text
    return text


def _norm(s: str) -> str:
    return re.sub(r'[^a-z0-9_@.\-]', '', s.lower())


def _contains_rid(text: str, rid: str) -> bool:
    """OCR-tolerant containment of a resource id (O/0, l/1, I/1 confusions)."""
    flat = text.lower().replace(' ', '')
    if rid in flat:
        return True
    head, _, tail = rid.rpartition('_')
    pat = head + '[_ ]?' + ''.join({'0': '[0oO]', '1': '[1lI|]', '5': '[5sS]', '8': '[8B]'}.get(ch, ch) for ch in tail)
    return re.search(pat, flat, re.I) is not None


def _contains_alias(text: str, aliases: List[str]) -> bool:
    flat = re.sub(r'[^a-z0-9]', '', text.lower())
    for a in aliases:
        core = re.sub(r'[^a-z0-9]', '', re.sub(r'\.\w{2,4}$', '', a.lower()))
        if len(core) >= 6 and core in flat:
            return True
    return False


def _state_for(prefix: str, text: str, facts: Optional[Dict] = None) -> Tuple[str, Dict]:
    """Heuristic (OCR keywords) state of the main record shown, refined by model-read facts when available."""
    facts = facts or {}
    low = text.lower()
    attrs: Dict = {}
    if facts.get('record_present') is False:
        return 'NOT_FOUND', attrs
    if prefix == 'data_flow':
        active = facts.get('data_flow_active')
        if active is None:
            active = not re.search(r'\b(paused|disabled|stopped|inactive)\b', low)
        return ('ACTIVE' if active else 'DISABLED'), attrs
    if prefix == 'backup':
        possible = facts.get('restore_possible')
        if possible is None:
            possible = not re.search(r'restore (blocked|disabled|denied)|deletion[ _-]block', low)
        return ('ACTIVE' if possible else 'BLOCKED'), attrs
    if prefix == 'analytics':
        return ('ANONYMIZED' if facts.get('shows_direct_identifiers') is False else 'IDENTIFIABLE'), attrs
    if prefix == 'support':
        return ('REDACTED' if facts.get('shows_direct_identifiers') is False else 'IDENTIFIABLE'), attrs
    if prefix == 'tombstone':
        return ('IDENTIFIABLE' if facts.get('shows_direct_identifiers') is True else 'REDACTED'), attrs
    if prefix == 'object':
        attrs['attachment'] = True
    return 'FOUND', attrs


def extract_artifacts(dataset_dir: str, manifest: Dict[str, Dict[str, str]],
                      activity: List[Dict[str, str]], identity_emails: Dict[str, set],
                      facts_fn: Optional[Callable[[str], Optional[Dict]]] = None) -> List[Obs]:
    """Observations from PDFs and screenshots."""
    obs: List[Obs] = []
    files = {}
    for fid, m in manifest.items():
        ext = os.path.splitext(m['file_path'])[1].lower()
        if ext in ('.pdf', '.png', '.jpg', '.jpeg'):
            files[fid] = (os.path.join(dataset_dir, m['file_path']), m['captured_at'], ext)
    texts = {fid: extract_text(p) for fid, (p, _, _) in files.items()}

    # 1. catalog links: rid <-> (email, user) pinned to the artifact captured just before the link
    for x in activity:
        if x['event_type'] != 'LINK_RESOURCE_IDENTITY':
            continue
        rid = x['resource_id']
        emails = EMAIL_RE.findall(x['details'])
        users = re.findall(r'user_\d+', x['details'])
        t = datetime.strptime(x['event_time'], FMT)
        aliases = re.findall(r'\(([^)]+)\)', x['details'])
        best: Optional[str] = None
        for fid, (_, cap, _) in files.items():
            dt = (t - datetime.strptime(cap, FMT)).total_seconds()
            unreadable = not texts[fid].strip()          # no OCR available: fall back to the catalog link + capture time
            if 0 <= dt <= 900 and (unreadable or _contains_rid(texts[fid], rid) or _contains_alias(texts[fid], aliases)):
                if best is None or cap > files[best][1]:
                    best = fid
        if best is None:
            continue
        pfx = rid_prefix(rid)
        facts = facts_fn(files[best][0]) if facts_fn else None
        result, attrs = _state_for(pfx, texts[best], facts)
        ids: Dict[str, str] = {}
        if users:
            ids['ref'] = users[0]
        if emails and pfx != 'tombstone':
            ids['email'] = emails[0]
        obs.append(Obs(best, rid, rid_system(rid) or '', files[best][1], 'artifact', result, ids,
                       {**attrs, 'link': x['activity_id']}, key=(emails[0] if emails else '')))

    # 2. documents that carry a resource id and an identity of their own (e.g. a support attachment)
    for fid, (path, cap, ext) in files.items():
        if ext != '.pdf':
            continue
        text = texts[fid]
        if 'attachment' not in text.lower():
            continue
        rids = {m.group(0) for m in RID_RE.finditer(text)}
        emails = [e for e in EMAIL_RE.findall(text) if e.lower() in identity_emails]
        for rid in rids:
            if rid_prefix(rid) != 'support':
                continue
            ids = {'email': emails[0]} if emails else {}
            obs.append(Obs(fid, rid, rid_system(rid) or '', cap, 'artifact', 'FOUND', ids,
                           {'attachment': True}, key=ids.get('email', '')))
    return obs
