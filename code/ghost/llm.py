"""Optional LLM layer (Google Gemini). Everything is deterministic without it.

* API key is read from GEMINI_API_KEY / GOOGLE_API_KEY (never stored in the repository).
* Responses are cached on disk (keyed by model + prompt + attachments) so reruns are reproducible and free.
* Every call is recorded in a usage ledger that `usage.py` turns into evaluation/usage_report.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
CODE_DIR = os.path.dirname(HERE)
PROMPT_DIR = os.path.join(CODE_DIR, 'prompts')
CACHE_DIR = os.path.join(CODE_DIR, '.cache', 'llm')

DEFAULT_MODEL = 'gemini-2.5-flash'


def load_prompt(name: str) -> str:
    with open(os.path.join(PROMPT_DIR, name), encoding='utf-8') as fh:
        return fh.read().strip()


class LLM:
    """Thin, cache-first Gemini client returning parsed JSON."""

    def __init__(self, model: Optional[str] = None, cache_dir: str = CACHE_DIR, enabled: bool = True):
        self.model = model or os.environ.get('GEMINI_MODEL', DEFAULT_MODEL)
        self.cache_dir = cache_dir
        self.ledger: List[Dict[str, Any]] = []
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
        from google.genai import types
        contents: List[Any] = [user]
        for a in attachments:
            contents.append(types.Part.from_bytes(data=a['bytes'], mime_type=a['mime']))
        cfg = types.GenerateContentConfig(system_instruction=system, temperature=0.0,
                                          response_mime_type='application/json')
        last_err: Optional[Exception] = None
        for attempt in range(4):
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
            except Exception as e:                     # network / quota / malformed JSON -> retry, then degrade
                last_err = e
                time.sleep(2 ** attempt)
        self.ledger.append({'purpose': purpose, 'model': self.model, 'cached': False, 'error': str(last_err)[:200],
                            'input_tokens': 0, 'output_tokens': 0})
        return None

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
