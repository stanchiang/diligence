# Diligence

A local pitch-deck understanding tool using your Qwen vision model in LM Studio.

The first workflow imports a PDF, extracts the text and renders every slide, asks Qwen to describe the image and read visible text, combines the observations with the extracted text, then collates all slide interpretations into a face-value business explanation. It does not score investments or use external research.

## Run locally

Requires Python 3.9+, Poppler, and LM Studio with a Qwen model that supports images and tool calls. The installed `qwen/qwen3.6-35b-a3b` model was used for the local smoke checks.

```sh
python3 -m pip install -r requirements.txt
brew install poppler  # macOS, only if pdftoppm is not installed
lms server start --port 1234 --bind 127.0.0.1
./run.sh
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080), select your Qwen vision model, and click **Understand this deck**. Load Qwen in LM Studio first if it is not already available. The default deck is `CHARGEBOTIC_Deck - V3 .pdf`, if present in the repository root. It is imported automatically, with all 71 slides; inference starts when you click the analysis button.

For other decks, choose **Import pitch deck**. Limits: 25 MB and 150 slides. Image-only PDFs are supported through Qwen vision. A large Q8 model and a long deck can take considerable time. Progress and results are saved after every vision, fusion, and collation stage. Stop waits for the current local inference request to finish. Resume continues from saved checkpoints. A server restart marks unfinished runs as interrupted.

```sh
./run.sh --port 8081 --data /path/to/local/data
./run.sh --no-default
```

Optional environment variables:

- `LM_STUDIO_URL`: loopback HTTP API URL ending in `/v1`; defaults to `http://127.0.0.1:1234/v1`.
- `LM_STUDIO_API_KEY`: your local LM Studio server token if authentication is enabled. It stays in the Python process, never in the browser.

## Context engineering

- **Separate evidence channels:** PDF text, independent visual observations, and combined interpretation remain distinct and reviewable.
- **Fresh contexts:** each slide image goes to a dedicated vision call; fusion gets only that slide's text and vision result. Previous slides never accumulate in its context.
- **Structured working memory:** schema-validated JSON is checkpointed in SQLite. Invalid output receives one repair attempt and never becomes working memory.
- **Hierarchical collation:** six-slide groups produce summaries carrying original slide numbers. Additional local compression reduces large summaries before final synthesis.
- **Just-in-time retrieval:** the final synthesis can call `read_slide` and `search_slides`, implemented locally. These tools expose only this run's processed interpretations. LM Studio's unrelated chat/MCP tools are not imported automatically.
- **Bounded context:** text requests are limited to 48 KB of UTF-8 input; final evidence is compacted at 30 KB. Tool exploration is limited to one round with at most four calls; each tool result is limited to 2.4 KB with explicit truncation markers. These are conservative text limits, not exact model token counts. Actual token usage and latency are recorded; images and output tokens also consume the model context. Use a loaded context of at least 32K tokens; the installed model had a larger context during verification.
- **Per-request inference controls:** `reasoning_effort=none` keeps this extraction/explanation workflow from spending its output budget on internal reasoning. This setting was verified with the installed Qwen model and does not change your global LM Studio settings.
- **Provenance:** each run snapshots the deck SHA-256, model ID, renderer resolution, and prompt version. Every substantive final statement carries slide references; out-of-range references are rejected.
- **Clear uncertainty:** unreadable text, concept illustrations, projections, contradictory details, and missing information are called out in the prompts and retained in structured results. Valid citations establish a source pointer, not automatic factual or semantic verification.

See [architecture](docs/architecture.md) for the data flow and extension points. The design follows [LM Studio tool use](https://lmstudio.ai/docs/developer/openai-compat/tools) and [effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents).

## Privacy and storage

All rendering, inference, tools, storage and exports run locally. The app binds to `127.0.0.1`. Model URLs must be loopback addresses; inference ignores HTTP proxies and refuses redirects. No cloud APIs, external scripts, hosted fonts, analytics, or cloud OCR are used.

`data/` contains the SQLite database, source PDFs, rendered JPEGs, interpretations and traces. It is ignored by Git, as are PDFs. Exports contain deck content; keep them private. Stored source text and generated explanations are rendered as text rather than executable HTML. Local mutations require an application token and matching origin.

The coding assistant must not inspect real deck content or generated business interpretations. `AGENTS.md` preserves this constraint. Real-deck checks print metadata only; synthetic fixtures are used for content inspection.

## Verification

```sh
python3 -m unittest discover -s tests -v
node --check web/app.js
node --test tests/test_api.cjs
```

The tests cover text/vision fusion, tool call/result pairing, checkpoint recovery, citation rejection, input validation, local-only URLs, image-only PDF ingestion, deduplication, and HTTP boundaries.

Optional real-model smoke check after importing the default deck:

```sh
python3 -m tests.smoke_pipeline
python3 -m tests.smoke_tools
```

The pipeline smoke check processes only the first slide in a separate partial-deck test database under `data/smoke/`. It does not create a full-deck report and prints only statuses and validation metadata. The tool smoke check uses synthetic evidence to verify native function-call parsing, local execution and the model's response to a tool result.
