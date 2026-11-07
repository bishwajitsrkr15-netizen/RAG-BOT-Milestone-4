# Implementation Plan: Mutual Fund FAQ Assistant

This plan organizes implementation into the requested phases: project directory, document scraping, document loading, chunking, embedding, vector storage, retrieval and UI, followed by guardrails and tests. Keep each data stage persisted and inspectable.

## 1. Create the project directory

### Suggested structure

```text
project/
  config/
    app.yaml
  data/
    raw/
    loaded/
    chunks/
    embeddings/
    chroma/
    manifests/
  src/
    ingestion/
    retrieval/
    guardrails/
    ui/
  tests/
    fixtures/
    unit/
    integration/
  .env.example
  .gitignore
  README.md
  sources.md
  sample-qa.md
```

### Setup tasks

- Choose a lightweight language/framework and create its dependency manifest, test setup, and run commands.
- Put source URLs, storage paths, embedding model, vector database, LLM provider/model, and retrieval settings in `config/app.yaml`.
- Configure the initial embedding model as `sentence-transformers/all-MiniLM-L6-v2`, vector database as ChromaDB, and provider as Groq.
- Read `GROQ_API_KEY` and `LLM_MODEL` from a local `.env`; document `qwen/qwen3.8-27b` as the requested initial model. Add only placeholders to `.env.example`.
- Ignore `.env`, generated data, and local ChromaDB files in `.gitignore`.
- Configure only the five approved Groww URLs from the PRD. Do not follow links from those pages.

**Output:** runnable project skeleton, validated non-secret config, and documented setup.

## 2. Scrape source documents

### Implementation

1. Read the configured URL allowlist and reject unlisted URLs.
2. Fetch each page with request timeouts, response validation, and a bounded response size.
3. Do not crawl, discover, or fetch links from the pages. Validate redirects against the allowlist policy.
4. Save each successful raw response (HTML) under `data/raw/`, keyed by stable source ID, with metadata containing its URL, fetch timestamp, content type, and checksum.
5. Record expected, fetched, and failed page counts in a run manifest. Mark incomplete runs as partial.

**Edge cases:** blocked pages, HTTP errors, empty responses, unexpected redirects, and rate limits must be reported.

**Tests:** only approved URLs are fetched; no internal links are requested; raw documents and metadata are saved; failures are visible; no secrets or user questions enter artifacts.

## 3. Load scraped documents

This phase reads the saved files only; it performs no network requests.

### Implementation

1. Read the five configured source IDs and URLs from `config/app.yaml`.
2. Load each matching `data/raw/<source-id>.html` file and its metadata JSON file.
3. Validate source ID, exact configured URL, fetch timestamp, file size, and SHA-256 checksum before parsing.
4. Extract readable text, title, and any verifiable source update date. Do not treat fetch time as a source update date.
5. Save one record per source in `data/loaded/pages.jsonl`, including source URL, title, content, fetch/update timestamps, raw/content hashes, and status.
6. Save a manifest to `data/manifests/load-latest.json`.
7. Publish the new `pages.jsonl` only when all five sources load successfully. On a partial run, leave the prior loaded corpus unchanged and report each failure.

**Edge cases:** missing or empty raw files, malformed metadata, mismatched source URL/ID, checksum mismatch, oversized files, malformed HTML, and pages with no readable text.

**Tests:** loading makes no network requests; records trace to configured sources; invalid/missing files mark the run partial; failed loads do not overwrite the previous good corpus; identical source files yield stable content hashes.

## 4. Chunk the documents

### Implementation

1. Read successful page records from the loading stage.
2. Remove navigation and repeated boilerplate while preserving fact labels, values, headings, and table context.
3. Split by headings, paragraphs, and table rows before applying a maximum chunk size.
4. Keep each fact with its scheme name and qualifiers; do not combine separate schemes into one chunk.
5. Attach a stable chunk ID, source ID, URL, title, section/position, content hash, and source date.
6. Save chunks to `data/chunks/chunks.jsonl`, along with a manifest containing counts and source coverage.

**Edge cases:** empty pages, unusually long sections, facts split across adjacent table cells, and repeated boilerplate.

**Tests:** no empty chunks; every chunk traces to one allowed source; example facts remain associated with the right scheme; identical input yields stable IDs; changed content updates IDs.

## 5. Embed the chunks

### Implementation

1. Load `sentence-transformers/all-MiniLM-L6-v2` from configuration.
2. Embed chunks in batches and map each vector to its chunk ID.
3. Save vectors and a manifest to `data/embeddings/`. Record model name, available revision, dimensions, and chunk count.
4. Validate that vectors are finite, non-empty, and have a consistent dimension.
5. Use the same model and preprocessing for user-question embeddings.
6. Require re-embedding/re-indexing if the model or vector dimensions change.

**Edge cases:** model download/load errors, empty input, invalid vectors, and batch output count mismatch must stop the stage with a clear error.

**Tests:** vector count matches chunk count; vectors map to the correct IDs; query and document embedding configuration match; invalid output is rejected.

## 6. Store vectors in ChromaDB

### Implementation

1. Create a persistent ChromaDB collection using the configured path, collection name, and documented distance metric.
2. Store each embedding with its stable chunk ID, text, URL, and source/chunk metadata.
3. Use deterministic IDs and upsert so reruns do not duplicate unchanged content.
4. Update changed content and remove obsolete records only during a successful, explicit rebuild.
5. Do not replace the last known-good index when ingestion is partial or invalid.
6. Save a final ingestion manifest with stage counts, model/config identifiers, timestamps, and per-source status.

**Edge cases:** interrupted writes, stale records, partial source failures, incompatible embedding models, and ChromaDB unavailable.

**Tests:** repeated ingestion keeps record count stable; changed chunks update; metadata is retrievable; incompatible model settings cannot silently reuse the collection; partial runs preserve the previous good index.

## 7. Retrieval logic and user interface

### Retrieval and answer flow

1. Accept a text question without requiring a user account or identity.
2. Run guardrails before embedding or calling the LLM.
3. Embed an eligible factual question with the configured corpus embedding model.
4. Query the configured ChromaDB collection for a small configurable `top_k`.
5. Apply a relevance threshold calibrated for the chosen distance metric. If no relevant evidence is found, respond that the fact was not found in available sources.
6. Send only the question and relevant retrieved excerpts to the configured LLM provider (Groq initially). The prompt must require evidence-only, facts-only answers and must not ask the model to generate URLs.
7. Validate the response, then attach exactly one source link from retrieved metadata in application code.
8. Include `Last updated from sources: ` followed by a verifiable source date or a clear statement that the source update date is unavailable. Do not confuse fetch time with source update time.
9. Keep answers to three sentences maximum. Do not calculate or compare returns.

### UI

Include:

- A welcome line and three example factual questions.
- The disclaimer: **“Facts-only. No investment advice.”**
- A question field and loading state.
- A concise answer with exactly one citation link and source freshness text.
- Clear states for refusal, no evidence, privacy warning, and service errors.

Do not persist question history or display raw errors, credentials, or local file paths.

**Tests:** factual queries retrieve relevant scheme evidence; unknown queries avoid unsupported generation; citations point to retrieved sources; freshness wording and sentence limits are enforced; UI shows required content and error states.

## 8. Guardrails, edge cases, and tests

### Guardrail behavior

| Case | Expected behavior | Test |
|---|---|---|
| Buy/sell, suitability, or portfolio advice | Politely refuse advice and provide one relevant educational link when available. Do not call the LLM. | Verify refusal wording, one link, and zero LLM calls. |
| Returns or performance comparison | Do not calculate or compare performance. Link to an approved factsheet if available. | Verify no calculation/generation call and only an approved link is used. |
| PAN, Aadhaar, account number, OTP, email, or phone supplied | Do not repeat, log, store, embed, or send the value to the LLM; show a short privacy notice. | Use representative sensitive-input fixtures and assert no downstream calls or persistence. |
| Unsupported or low-relevance fact | Say it was not found in the available sources; do not guess. | Verify below-threshold retrieval does not call the LLM. |
| Conflicting retrieved facts | Disclose uncertainty rather than choosing an unsupported value. | Provide conflicting fixture chunks and verify safe response. |
| Missing citation metadata | Do not return an uncited factual answer; provide a safe fallback/error. | Verify every successful answer includes exactly one allowed URL. |
| Missing source update date | State that a reliable source update date is unavailable. | Verify fetch time is not presented as source update time. |
| Missing API key, timeout, provider error, or malformed output | Surface a clear operational error or safe fallback; never claim success. | Mock each failure and assert explicit handling. |
| Malicious instructions embedded in page text | Treat retrieved text as evidence, not instructions. | Fixture prompt-injection text and verify it does not change policy or trigger tools. |
| HTML/script content in source or answer | Render as escaped text, not executable markup. | UI test verifies markup is inert. |
| Scrape/parse partial failure | Mark run partial and preserve the last known-good index. | Simulate one failed page during ingestion. |
| Changed model/dimensions | Require re-embedding and collection rebuild/versioning. | Assert incompatible index reuse is rejected. |

### General test cases

- **Unit tests:** config validation, URL allowlist, extraction, chunk boundaries/IDs, embedding shape, privacy detection, intent routing, citation selection, freshness formatting, sentence limit.
- **Pipeline integration:** controlled page fixtures through loading → chunking → embedding → ChromaDB; inspect saved artifacts and rerun for idempotency.
- **Retrieval integration:** known factual questions retrieve the right scheme; unrelated questions return no-evidence behavior.
- **UI checks:** welcome line, three examples, disclaimer, citation, refusal, privacy, no-evidence, and error states.
- **Provider tests:** mock successful and failed Groq responses; routine tests must not require live credentials.

## 9. Completion checklist

- [ ] Data-loading, chunking, embedding, and vector-store outputs are separately inspectable.
- [ ] Only the five configured URLs are ingested; no linked pages are crawled.
- [ ] ChromaDB is persistent and ingestion is idempotent.
- [ ] Retrieval uses the same embedding model as ingestion and has a tested relevance threshold.
- [ ] Each factual answer is supported, at most three sentences, has exactly one source link, and includes source freshness text.
- [ ] Advice, performance, PII, low-evidence, and provider-failure guardrails pass tests.
- [ ] README, source list, sample Q&A, and demo link or short video are prepared.
