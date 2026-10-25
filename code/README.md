# Ghost Data Still Exists - deletion-audit agent

For every request in `dataset/deletion_requests.csv` the agent decides whether deletion was really requested, rebuilds the
person's identifiers (current **and** historical), finds every copy that the evidence shows at `audit_at`, applies
`deletion_rules.csv`, and writes an ordered `FINDING -> REMEDIATION -> VERIFICATION` plan to `output.csv`.

## Setup

```bash
python3 --version                 # 3.10+
sudo apt-get install -y tesseract-ocr poppler-utils   # OCR for screenshots, text for PDFs (macOS: brew install tesseract poppler)
pip install -r code/requirements.txt                  # only needed for the optional Gemini layer
```

No secrets live in the repository. Copy `.env.example` to `.env` (repo root or `code/`) and fill in your key - the file is
read automatically, never committed (`.gitignore`) and never packaged. Real environment variables, if set, take priority.
`GEMINI_API_KEY` (or `GOOGLE_API_KEY`) enables the optional model layer; `GEMINI_MODEL` overrides the model
(default `gemini-3.8-flash`).

## Run

```bash
python3 code/main.py                      # reads dataset/, writes output.csv (518 rows, sorted) + code/evaluation/usage_report.md
python3 code/main.py --llm off            # fully deterministic, never calls a model
python3 code/main.py --llm on             # Gemini for undecidable texts + screenshots (~20 calls; key read from .env)
python3 code/main.py --llm on --llm-audit # + batched second opinion on all requests (~21 more calls)
python3 code/evaluation/main.py           # metrics on the 32 public samples + 16 custom cases, regenerates evaluation_report.md
python3 code/package.py                   # builds code.zip (no dataset, caches or secrets)
```

A clean run takes well under a minute and is deterministic (stable ordering, compact JSON, no randomness).

## Architecture (`code/ghost/`)

| Module | Job |
| --- | --- |
| `logparse.py` | Six log dialects (key=value, JSON lines, syslog, bracketed, pipe-separated, cloud runtime) -> flat records |
| `evidence.py` | Every SQL / JSON / TXT / log file -> normalised observations `Obs(file, resource, time, result, identifiers, attrs)` plus *coverage* (which snapshots enumerate which system). SQL files are executed with SQLite; replica tables are kept per topology so drift is visible |
| `artifacts.py` | PDFs (`pdftotext`) and screenshots (Tesseract, optional Gemini vision) -> observations, pinned to the catalog link events |
| `identity.py` | Request e-mail -> user; current + historical e-mail / hash / phone (SHA-256 of every known address) |
| `classify.py` | Multilingual deletion-intent rules (negations such as "do not close the account" win); LLM only adjudicates unsure texts |
| `engine.py` | Linking, state at `audit_at`, policy, assignment of unidentifiable rows, plan builder |
| `scoring.py`, `usage.py`, `llm.py` | Metrics, usage/cost report, cached Gemini client |

### Investigation approach

1. **Authorization** - only a text that asks for deletion/erasure is audited; everything else is `NOT_APPLICABLE` with `[]`.
2. **Identity** - exact, normalised matching of e-mail (current + old), e-mail hash (current + old), phone and the stable
   `user_id`. Look-alike addresses never match. Names are never used to link.
3. **Evidence window** - an observation counts for a case only when `requested_at - 1 day <= observed <= audit_at`; files
   whose manifest `captured_at` is after `audit_at` are neither used for state nor cited.
4. **State reconstruction** - the latest observation of each resource wins; a violation beats a clean copy captured at the
   same moment (primary vs. replicas); a later *exhaustive* snapshot of the case's own batch that no longer lists the record
   proves removal. Workflow statuses (`reported_status`, `reported_result`) are never trusted.
5. **Policy** - per resource kind: account/search/export/marketing/attachment -> delete; support message -> redact;
   analytics -> anonymise; connector -> disable; archive -> block restore; audit/legal hold -> must still exist; tombstone ->
   keep `user_id`, no name/e-mail/phone/hash. `COMPLETE` only when nothing violates.
6. **Unidentifiable rows** - already-deleted accounts and anonymised analytics rows carry no identifier. They are tied to
   the case through the workflow log window around `reported_at` (result class must match `reported_status`, the row must not
   belong to someone else, and a compliant case must be able to prove it in a citable file); competing cases are solved
   jointly. Some pairs are genuinely interchangeable in the data - those use a documented deterministic tie-break.
7. **Plan** - findings (remaining -> exposed -> flow -> restore), remediation (disable flows, block restores, then
   delete, redact, anonymise - so dependants are handled before sources), verification of every final state incl. the
   retained audit record. Ordering inside a phase follows the operation table, then `system_id`, then `resource_id`.
   Evidence ids are filled only for findings and for `COMPLETE` verifications.

### Where the model is used

Rules and SQL do the bulk of the work (cheap, reproducible). Gemini is used where judgement is needed:
`prompts/classify_request.md` (adjudicates texts the rules cannot decide; with `--llm-audit`, a batched second opinion on every request) and
`prompts/artifact_facts.md` (reads screenshots / PDFs: is a connector active, can a backup be restored, are direct
identifiers visible). Responses are cached under `code/.cache/llm/`, calls and tokens are logged, and
`evaluation/usage_report.md` is regenerated from that ledger on every full run.

### Using the public samples

`sample_output.csv` is used (a) as the labelled validation set and (b) in the final run to learn which rows the 32 public
cases already own, so the evaluation cases do not claim them. Evaluation (`code/evaluation/main.py`) never uses the answers
it scores.

## Known limitations

See `evaluation/evaluation_report.md` section 6 (ambiguous interchangeable log rows; audit records that appear in no file;
OCR-based screenshot reading without a model).

### Free-tier quotas

Free Gemini keys allow only ~20 requests/day per model. The default run therefore needs just ~20 calls; a daily-quota (429) error
switches to the next fallback model, and when every model is exhausted the run continues rule-based. `GEMINI_MAX_CALLS=N` caps live calls.
