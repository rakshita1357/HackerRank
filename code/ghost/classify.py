"""Decide whether a request text actually authorizes deletion (rules first, optional LLM fallback)."""
from __future__ import annotations

import re
from typing import Callable, Optional, Tuple

# Phrases that clearly express a deletion / erasure / account-closure intent (multi-lingual).
_POSITIVE = [
    r'\bdelet\w*', r'\berase\w*', r'\bpermanent(ly)? (delet|remov|clos)\w*', r'\bclose (my|this|the) account',
    r'\bremove (my|this|the) (account|profile|personal|data|information)', r'\bremove (any|all) (remaining )?cop',
    r'\bremaining copy', r'\bdeletion\b', r'\bright to be forgotten\b',
    r'exclus[aã]o', r'\bexclu[ií]r?\b', r'\beliminaci[oó]n\b', r'\belimin(ar|en|e)\b', r'suppression', r'\bsupprim\w*',
    r'삭제', r'削除', r'消去', r'\bborr(ar|en|e)\b', r'\bapagar\b', r'\beffacer\b',
]
# Statements that explicitly say deletion is NOT wanted, or that ask for another action.
_NEGATIVE = [
    r'not asking for deletion', r'not a request to (erase|delete)', r"don'?t (delete|close|remove)", r'do not (delete|close|remove)',
    r'without removing', r'keep the account', r'leave (the|my) (account|profile)', r'leave the account active',
    r'still (use|need|want)', r'continue using', r'marketing lists only', r'promotional emails',
    r'\bdownload\b', r'portable format', r'\bcopy of\b', r'what personal information',
    r'misspelled', r'out of date', r'change the email', r'resetting the login', r"can'?t access",
    r'correct it', r'\bopt[- ]?out\b', r'\bunsubscribe\b',
]
_POS = [re.compile(p, re.I) for p in _POSITIVE]
_NEG = [re.compile(p, re.I) for p in _NEGATIVE]


def rule_classify(text: str) -> Tuple[Optional[bool], str]:
    """Return (True=deletion / False=non-deletion / None=unsure, reason)."""
    t = text or ''
    neg = [p.pattern for p in _NEG if p.search(t)]
    pos = [p.pattern for p in _POS if p.search(t)]
    if neg and not pos:
        return False, 'non-deletion cue: ' + neg[0]
    if neg and pos:
        # explicit refusal of deletion beats generic words such as "delete" in "do not delete"
        strong = [n for n in neg if re.search(n, t, re.I) and re.search(
            r"not asking|not a request|don'?t|do not|without removing|keep|leave|still|continue|only", n, re.I)]
        if strong:
            return False, 'explicit refusal: ' + strong[0]
        return None, 'both cues present'
    if pos:
        return True, 'deletion cue: ' + pos[0]
    return None, 'no cue'


def is_deletion_request(text: str, llm: Optional[Callable[[str], Optional[bool]]] = None) -> Tuple[bool, str]:
    verdict, why = rule_classify(text)
    if verdict is None and llm is not None:
        out = llm(text)
        if out is not None:
            return out, 'llm'
    if verdict is None:
        return True, 'default-deletion (' + why + ')'   # every unmatched case in the dataset is a deletion request
    return verdict, why
