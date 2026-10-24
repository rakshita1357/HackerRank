# Usage report

Summary of the final full-dataset run that produced `output.csv` (518 requests, `python3 code/main.py`).

**Model providers / names:** none - no model call was made in this run.

This run used the deterministic pipeline only (rules, SQLite, OCR with Tesseract/pdftotext). The optional Gemini layer was not enabled because `GEMINI_API_KEY` was not set.

| Metric | Value |
| --- | --- |
| Model calls | 0 |
| Input tokens | 0 |
| Output tokens | 0 |
| Total tokens | 0 |
| Avg tokens / request | 0 |
| Estimated total cost | $0.00 |
| Estimated cost / request | $0.00 |

To reproduce the model-assisted run: `export GEMINI_API_KEY=...` and run `python3 code/main.py --llm on`; this file is regenerated automatically from the call ledger.
