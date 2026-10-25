"""Build evaluation/usage_report.md from the LLM call ledger of the final full run."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
CODE_DIR = os.path.dirname(HERE)


def load_pricing() -> Dict[str, Dict[str, float]]:
    with open(os.path.join(CODE_DIR, 'config.json'), encoding='utf-8') as fh:
        return json.load(fh)['pricing_usd_per_million_tokens']


def render(ledger: List[Dict[str, Any]], n_requests: int, model_hint: str, llm_enabled: bool) -> str:
    pricing = load_pricing()
    per: Dict[str, Dict[str, float]] = defaultdict(lambda: {'calls': 0, 'live': 0, 'replayed': 0, 'errors': 0,
                                                            'input': 0, 'output': 0})
    by_purpose: Dict[str, int] = defaultdict(int)
    for e in ledger:
        m = per[e.get('model', model_hint)]
        m['calls'] += 1
        m['replayed' if e.get('cached') else 'live'] += 1
        m['errors'] += 1 if e.get('error') else 0
        m['input'] += e.get('input_tokens', 0)
        m['output'] += e.get('output_tokens', 0)
        by_purpose[e.get('purpose', '?')] += 1
    lines = ['# Usage report', '',
             'Summary of the final full-dataset run that produced `output.csv` '
             f'({n_requests} requests, `python3 code/main.py`).', '']
    if not ledger:
        lines += ['**Model providers / names:** none - no model call was made in this run.', '',
                  'This run used the deterministic pipeline only (rules, SQLite, OCR with Tesseract/pdftotext). '
                  + ('The LLM layer was available but no call was needed.' if llm_enabled else
                     'The optional Gemini layer was not enabled because `GEMINI_API_KEY` was not set.'), '',
                  '| Metric | Value |', '| --- | --- |', '| Model calls | 0 |', '| Input tokens | 0 |',
                  '| Output tokens | 0 |', '| Total tokens | 0 |', '| Avg tokens / request | 0 |',
                  '| Estimated total cost | $0.00 |', '| Estimated cost / request | $0.00 |', '',
                  'To reproduce the model-assisted run: `export GEMINI_API_KEY=...` and run `python3 code/main.py --llm on`; '
                  'this file is regenerated automatically from the call ledger.']
        return '\n'.join(lines) + '\n'
    lines += ['**Provider:** Google (Gemini API). **Prices:** list-price estimates from `code/config.json`.', '',
              '| Model | Calls | Live | Replayed from cache | Input tokens | Output tokens | Total tokens | Est. cost (USD) |',
              '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    tot = {'calls': 0, 'live': 0, 'replayed': 0, 'input': 0, 'output': 0, 'cost': 0.0}
    for model, m in sorted(per.items()):
        price = pricing.get(model, {'input': 0.0, 'output': 0.0})
        cost = m['input'] / 1e6 * price['input'] + m['output'] / 1e6 * price['output']
        lines.append(f"| {model} | {m['calls']:.0f} | {m['live']:.0f} | {m['replayed']:.0f} | {m['input']:,.0f} | "
                     f"{m['output']:,.0f} | {m['input'] + m['output']:,.0f} | ${cost:.4f} |")
        for k in ('calls', 'live', 'replayed', 'input', 'output'):
            tot[k] += m[k]
        tot['cost'] += cost
    total_tokens = tot['input'] + tot['output']
    lines += [f"| **Overall** | {tot['calls']:.0f} | {tot['live']:.0f} | {tot['replayed']:.0f} | {tot['input']:,.0f} | "
              f"{tot['output']:,.0f} | {total_tokens:,.0f} | ${tot['cost']:.4f} |", '',
              '| Metric | Value |', '| --- | --- |',
              f'| Requests processed | {n_requests} |', f"| Model calls | {tot['calls']:.0f} |",
              f'| Total tokens | {total_tokens:,.0f} |',
              f'| Average tokens / request | {total_tokens / max(n_requests, 1):,.1f} |',
              f"| Estimated total cost | ${tot['cost']:.4f} |",
              f"| Estimated cost / request | ${tot['cost'] / max(n_requests, 1):.6f} |", '',
              'Calls by purpose: ' + ', '.join(f'{k} = {v}' for k, v in sorted(by_purpose.items())) + '.', '',
              'Token counts come from the API `usage_metadata`; "replayed" calls were served from the on-disk cache and '
              'carry the token counts recorded when they were first made.']
    return '\n'.join(lines) + '\n'
