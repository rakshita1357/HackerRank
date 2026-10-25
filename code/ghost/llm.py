"""Optional LLM layer (Google Gemini). Everything is deterministic without it.

* API key is read from GEMINI_API_KEY / GOOGLE_API_KEY (never stored in the repository).
* Responses are cached on disk (keyed by model + prompt + attachments) so reruns are reproducible and free.
* Every call is recorded in a usage ledger that `usage.py` turns into evaluation/usage_report.md.
"""
from __future__ import annotations

import hashlib
import re
import logging
import sys
import threading
import json
import os
import time
from typing import Any, Dict, List, Optional

from .envfile import load_env

HERE = os.path.dirname(os.path.abspath(__file__))
CODE_DIR = os.path.dirname(HERE)
PROMPT_DIR = os.path.join(CODE_DIR, 'prompts')
CACHE_DIR = os.path.join(CODE_DIR, '.cache', 'llm')

DEFAULT_MODEL = 'gemini-3.8-flash'
# tried in order when the configured model answers 404 (retired / not available to this key)
FALLBACK_MODELS = ['gemini-3.8-flash', 'gemini-flash-latest']   # then whatever the key can list (see _available_models)
logging.getLogger('google_genai').setLevel(logging.ERROR)
logging.getLogger('google_genai.models').setLevel(logging.ERROR)


def load_prompt(name: str) -> str:
    with open(os.path.join(PROMPT_DIR, name), encoding='utf-8') as fh:
        return fh.read().strip()


class LLM:
    """Thin, cache-first Gemini client returning parsed JSON."""

    def __init__(self, model: Optional[str] = None, cache_dir: str = CACHE_DIR, enabled: bool = True):
        load_env()                      # picks up GEMINI_API_KEY / GEMINI_MODEL from a local .env file
        self.model = model or os.environ.get('GEMINI_MODEL', DEFAULT_MODEL)
        self.cache_dir = cache_dir
        self.ledger: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self.disabled_reason = ''
        self.live_calls = 0
        self.max_calls = int(os.environ.get('GEMINI_MAX_CALLS', '0') or 0)   # 0 = unlimited
        self._client = None
        key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
        self.enabled = bool(enabled and key)
        self._key = key
        os.makedirs(self.cache_dir, exist_ok=True)

    # ---- plumbing ---------------------------------------------------------------------------
    def _client_obj(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self._key)
        return self._client

    def _cache_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, key + '.json')

    def json_call(self, purpose: str, system: str, user: str, attachments: Optional[List[Dict[str, Any]]] = None
                  ) -> Optional[Dict[str, Any]]:
        """Run one JSON-mode call. `attachments` = [{'mime': 'image/png', 'bytes': b'...'}]."""
        attachments = attachments or []
        h = hashlib.sha256()
        for part in (self.model, system, user):
            h.update(part.encode('utf-8'))
        for a in attachments:
            h.update(a['mime'].encode())
            h.update(hashlib.sha256(a['bytes']).digest())
        key = h.hexdigest()
        path = self._cache_path(key)
        if os.path.exists(path):
            with open(path, encoding='utf-8') as fh:
                hit = json.load(fh)
            self.ledger.append({'purpose': purpose, 'model': hit.get('model', self.model), 'cached': True,
                                'input_tokens': hit.get('input_tokens', 0), 'output_tokens': hit.get('output_tokens', 0)})
            return hit.get('result')
        if not self.enabled:
            return None
        if self.max_calls and self.live_calls >= self.max_calls:
            self._disable(f'call budget of {self.max_calls} reached (GEMINI_MAX_CALLS)')
            return None
        self.live_calls += 1
        from google.genai import types
        contents: List[Any] = [user]
        for a in attachments:
            contents.append(types.Part.from_bytes(data=a['bytes'], mime_type=a['mime']))
        cfg = types.GenerateContentConfig(
            system_instruction=system, temperature=0.0, response_mime_type='application/json',
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))   # no tools: silences the AFC notice
        last_err: Optional[Exception] = None
        attempt = 0
        while attempt < 4 and self.enabled:
            try:
                resp = self._client_obj().models.generate_content(model=self.model, contents=contents, config=cfg)
                text = resp.text or ''
                result = json.loads(text)
                um = getattr(resp, 'usage_metadata', None)
                tin = int(getattr(um, 'prompt_token_count', 0) or 0)
                tout = int(getattr(um, 'candidates_token_count', 0) or 0) + int(getattr(um, 'thoughts_token_count', 0) or 0)
                with open(path, 'w', encoding='utf-8') as fh:
                    json.dump({'model': self.model, 'purpose': purpose, 'input_tokens': tin, 'output_tokens': tout,
                               'result': result}, fh, ensure_ascii=False)
                self.ledger.append({'purpose': purpose, 'model': self.model, 'cached': False,
                                    'input_tokens': tin, 'output_tokens': tout})
                return result
            except Exception as e:
                last_err = e
                code = getattr(e, 'code', None) or getattr(e, 'status_code', None)
                if code == 404 and self._next_model():          # model retired / unavailable: switch and retry at once
                    continue
                if code == 429 and self._daily_quota(e):        # free-tier daily cap is per model: try another, else stop
                    if self._next_model():
                        continue
                    self._disable('daily quota exhausted on every fallback model')
                    break
                if code in (400, 401, 403, 404):                # permanent: bad key, bad model, no access
                    self._disable(f'{code} {str(e)[:160]}')
                    break
                attempt += 1                                    # per-minute 429 / 5xx / malformed JSON: back off and retry
                time.sleep(min(self._retry_delay(e, 2 ** attempt), 30))
        self.ledger.append({'purpose': purpose, 'model': self.model, 'cached': False, 'error': str(last_err)[:200],
                            'input_tokens': 0, 'output_tokens': 0})
        return None

    @staticmethod
    def _daily_quota(e: Exception) -> bool:
        s = str(e)
        return 'PerDay' in s or 'per day' in s.lower() or bool(re.search(r"retryDelay'?: ?'?(\d{3,})s", s))

    @staticmethod
    def _retry_delay(e: Exception, default: float) -> float:
        m = re.search(r"retryDelay'?: ?'?(\d+(?:\.\d+)?)s", str(e))
        return float(m.group(1)) + 1 if m else default

    def _next_model(self) -> bool:
        with self._lock:
            tried = getattr(self, '_tried', {self.model})
            for m in FALLBACK_MODELS:
                if m not in tried:
                    tried.add(m)
                    self._tried = tried
                    print(f'  [llm] model unavailable or over quota -> switching to {m}', file=sys.stderr, flush=True)
                    self.model = m
                    return True
            for m in self._available_models():
                if m not in tried:
                    tried.add(m)
                    self._tried = tried
                    print(f'  [llm] switching to listed model {m}', file=sys.stderr, flush=True)
                    self.model = m
                    return True
            self._tried = tried
        return False

    def _available_models(self) -> List[str]:
        """Flash-class models this key can actually call (newest names first)."""
        try:
            names = []
            for m in self._client_obj().models.list():
                acts = getattr(m, 'supported_actions', None) or []
                n = (getattr(m, 'name', '') or '').replace('models/', '')
                if 'flash' in n and 'generateContent' in acts and not any(x in n for x in ('image', 'tts', 'live', 'audio', 'thinking')):
                    names.append(n)
            return sorted(set(names), reverse=True)
        except Exception:
            return []

    def _disable(self, why: str) -> None:
        with self._lock:
            if self.enabled:
                self.enabled = False
                self.disabled_reason = why
                print(f'  [llm] Gemini disabled for the rest of this run ({why}). Continuing with the rule-based pipeline.',
                      file=sys.stderr, flush=True)

    def audit_batch(self, texts: List[str], size: int = 25) -> Dict[str, bool]:
        """Second opinion for many texts in few calls (free tiers allow only ~20 calls a day)."""
        uniq = sorted(set(texts))
        out: Dict[str, bool] = {}
        for i in range(0, len(uniq), size):
            chunk = uniq[i:i + size]
            body = '\n'.join(f'[{j}] {t}' for j, t in enumerate(chunk))
            res = self.json_call('classify_batch', load_prompt('classify_request.md') +
                                 '\nSeveral messages are given, each prefixed by [index]. Return strict JSON: '
                                 '{"results": [{"i": <index>, "deletion_requested": true|false}, ...]}', body)
            for r in (res or {}).get('results', []) if isinstance(res, dict) else []:
                try:
                    if isinstance(r['deletion_requested'], bool):
                        out[chunk[int(r['i'])]] = r['deletion_requested']
                except (KeyError, ValueError, IndexError, TypeError):
                    pass
            print(f'  [llm] audited {min(i + size, len(uniq))}/{len(uniq)} request texts', file=sys.stderr, flush=True)
            if not self.enabled:
                break
        return out

    def prefetch_classify(self, texts: List[str], workers: int = 6) -> None:
        """Warm the cache for many request texts in parallel, with a progress line."""
        from concurrent.futures import ThreadPoolExecutor
        uniq = sorted(set(texts))
        done = [0]
        lock = threading.Lock()

        def one(t: str) -> None:
            self.classify_request(t)
            with lock:
                done[0] += 1
                if done[0] % 25 == 0 or done[0] == len(uniq):
                    print(f'  [llm] classified {done[0]}/{len(uniq)} request texts', file=sys.stderr, flush=True)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, uniq))

    # ---- tasks -------------------------------------------------------------------------------
    def classify_request(self, text: str) -> Optional[bool]:
        out = self.json_call('classify_request', load_prompt('classify_request.md'), 'Customer message:\n' + text)
        if isinstance(out, dict) and isinstance(out.get('deletion_requested'), bool):
            return out['deletion_requested']
        return None

    def artifact_facts(self, path: str) -> Optional[Dict[str, Any]]:
        ext = os.path.splitext(path)[1].lower()
        mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.pdf': 'application/pdf'}.get(ext)
        if not mime:
            return None
        with open(path, 'rb') as fh:
            data = fh.read()
        return self.json_call('artifact_facts', load_prompt('artifact_facts.md'),
                              'Extract the facts from the attached artifact.', [{'mime': mime, 'bytes': data}])
