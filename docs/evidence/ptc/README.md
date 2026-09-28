# PTC real-provider smoke evidence (2026-09-24)

Run with `LANG_HARNESS_REAL_LLM_EVIDENCE_DIR=docs/evidence/ptc` and the
`tests/test_ptc_real_provider.py::test_real_provider_ptc_add` opt-in test.
Evidence JSON contains protocol, error category/type and status only; the
NDJSON files are empty because all three requests failed before a stream event.
No API key or raw provider error body is stored here.

| Provider | Protocol | Current result |
| --- | --- | --- |
| `deepseek-v4-flash` | chat | HTTP 403, quota exhausted |
| `claude-fable5` | anthropic | HTTP 403, quota exhausted |
| `chatgpt-5` | responses | empty stream (`No generations found in stream`) |

These failures do not invalidate the separate 2026-09-23 chat and anthropic
successful smoke runs, but they do not prove current three-protocol acceptance.
The test can be rerun after external quota/endpoint issues are resolved.
