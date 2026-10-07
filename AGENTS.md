# Confidential deck processing

The user requires that pitch decks be understood exclusively by their local Qwen model in LM Studio.

- Do not view real deck images, read extracted deck text, or bring model-generated deck interpretations into the coding assistant's context.
- Local application code may extract text, render images, send them to the loopback LM Studio endpoint, and save/display results to the user.
- Verify real-deck processing through metadata only: slide counts, completion states, response-schema validation, citation integrity, latency, and tool-call counts.
- Use synthetic fixtures for content inspection and UI tests. Do not use cloud models, cloud OCR, remote inference, analytics, or hosted assets.
- Keep decks and all generated content under ignored `data/`. Never commit confidential PDFs or generated interpretations.
