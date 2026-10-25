"""Parsers for the six log dialects found in dataset/evidence/logs."""
from __future__ import annotations

import json
import re
from typing import Dict, Iterator, List

_KV = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("(?:[^"\\]|\\.)*"|\S+)')
_TS = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s+(.*)$')


def _unq(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    return v


def kv_pairs(text: str) -> Dict[str, str]:
    return {k: _unq(v) for k, v in _KV.findall(text)}


def parse_line(line: str) -> Dict[str, str] | None:
    """Return a flat dict with at least ts/service/event, or None."""
    line = line.rstrip('\n')
    m = _TS.match(line)
    if not m:
        return None
    ts, rest = m.group(1), m.group(2)
    rec: Dict[str, str] = {'ts': ts}
    # structured JSON
    if rest.startswith('{'):
        try:
            d = json.loads(rest)
        except ValueError:
            return None
        rec.update({k: ('' if v is None else str(v)) for k, v in d.items()})
        rec.setdefault('service', d.get('logger', ''))
        return rec
    # operations stream: "| LEVEL | service | trace | event | kv"
    if rest.startswith('| '):
        parts = [x.strip() for x in rest[2:].split(' | ', 4)]
        if len(parts) == 5:
            rec.update(level=parts[0], service=parts[1], trace_id=parts[2], event=parts[3])
            rec.update(kv_pairs(parts[4]))
            return rec
    # application trace: "[LEVEL] [service] [trace] event: kv"
    m2 = re.match(r'\[(\w+)\] \[([^\]]*)\] \[([^\]]*)\] ([\w]+): ?(.*)$', rest)
    if m2:
        rec.update(level=m2.group(1), service=m2.group(2), trace_id=m2.group(3), event=m2.group(4))
        rec.update(kv_pairs(m2.group(5)))
        return rec
    # syslog: "<15>1 node svc trace - - kv"
    m3 = re.match(r'<\d+>1 (\S+) (\S+) (\S+) - - (.*)$', rest)
    if m3:
        rec.update(node=m3.group(1), service=m3.group(2), trace_id=m3.group(3))
        rec.update(kv_pairs(m3.group(4)))
        return rec
    # cloud runtime: requestId=.. level=.. function=.. message="..."
    if rest.startswith('requestId='):
        outer = kv_pairs(rest)
        msg = outer.pop('message', '')
        rec.update(trace_id=outer.get('requestId', ''), level=outer.get('level', ''),
                   service=outer.get('function', ''))
        rec.update(kv_pairs(msg))
        return rec
    # plain key=value
    rec.update(kv_pairs(rest))
    return rec


def parse_log(path: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    with open(path, encoding='utf-8') as fh:
        for ln, line in enumerate(fh, 1):
            r = parse_line(line)
            if r is not None:
                r['_line'] = str(ln)
                out.append(r)
    return out
