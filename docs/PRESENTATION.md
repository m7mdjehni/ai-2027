# Kiln Presentation Outline

1. **Purpose:** local autonomous website project generation.
2. **Workflow:** brief, Ollama generation, saved files, static validation, and
	bounded repair.
3. **Architecture:** FastAPI, PostgreSQL, Windows helper, Ollama, validator, and
	isolated preview.
4. **Local models:** random local selection between `qwen2.5:1.5b` and
   `llama3.2:3b`, with context 2048, q4_0 KV cache, and one parallel request.
5. **Project data:** local files plus persisted project and run history.
6. **Safety boundaries:** local-only model routing, no external validator
	network, preview CSP, and inert source display.
7. **Demonstration:** follow [DEMO.md](DEMO.md) with an example project brief.
8. **Verification:** report only checks actually run; disclose Docker/database
	blockers and validation limitations.
9. **Offline test:** physical network disconnection remains a manual user test.
