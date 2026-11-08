# Architecture: Mutual Fund FAQ Assistant

## 1. Overview

The prototype is a small retrieval-augmented generation (RAG) application. A batch ingestion pipeline fetches a fixed allowlist of public pages, extracts and persists page text, creates traceable chunks, and embeds them. ChromaDB is a local staging index for the validated migration workflow; Qdrant Cloud is the runtime vector database used by both the Streamlit and Next.js applications. At question time, the application applies privacy and intent checks, retrieves relevant chunks from Qdrant, and generates a short answer grounded in those chunks.

The architecture prioritizes inspectable intermediate data and a small deployment footprint. It does not crawl page links or connect to user investment accounts.

## 2. System context and boundaries

### External systems

- **Approved public pages:** The five Groww pages listed in the PRD; they are the only initial ingestion targets.
- **Embedding model:** `sentence-transformers/all-MiniLM-L6-v2`, configured once and shared by ingestion and query-time retrieval.
- **Runtime vector store:** Qdrant Cloud, configured with a 384-dimensional cosine collection.
- **Local staging index:** ChromaDB, used by the ingestion pipeline before validated points are migrated to Qdrant.
- **LLM provider:** Groq for the initial prototype. The API key and selected model are supplied through environment variables, Streamlit secrets, or a local `.env` file.
- **Future provider adapters:** Gemini, OpenAI, Anthropic, and Mistral may be added behind the same LLM interface. They are not considered supported until their integrations are implemented and tested.

### In scope

- Ingesting only explicitly configured, allowlisted URLs.
- Persisting raw/extracted data, chunks, embeddings, vector records, and metadata for demonstration.
- Answering source-supported factual questions and safely refusing advice-oriented requests.
- Displaying the source link and freshness information with each response.

### Out of scope

- Following links from crawled pages or general-purpose web search.
- Investment recommendations, performance calculations, or personalized portfolio analysis.
- Account access or statement retrieval on behalf of a user.
- Persisting user questions or personal identifiers.

## 3. Logical components

| Component | Responsibility |
|---|---|
| Configuration | Loads the source allowlist, storage paths, embedding model, vector-store settings, provider, and model. Secrets are read from environment variables, not committed configuration. |
| Source loader | Fetches only allowlisted URLs, extracts readable content, and records the canonical source URL, retrieval timestamp, and available page date/update metadata. |
| Stage artifact store | Saves inspectable outputs for loading, chunking, and embedding under a configured data directory. It does not store user queries. |
| Chunker | Splits extracted content into semantically coherent, fact-preserving chunks and attaches source/page/location metadata. |
| Embedding service | Embeds corpus chunks and incoming questions using the same configured model. |
| Local staging repository | Persists and validates chunk embeddings in ChromaDB before migration. |
| Qdrant repository | Stores the runtime vectors and payloads; performs cosine search with optional source filtering. |
| Query guard | Rejects or redirects advice/performance requests and screens for personal/account identifiers before any LLM call. |
| Retriever | Retrieves the most relevant chunks and applies a configured relevance threshold before generation. |
| LLM adapter | Calls the configured provider using retrieved context and strict facts-only response instructions. |
| Answer validator | Enforces sentence limit, grounded citation selection, required freshness wording, and safe fallback behavior. |
| UI | Presents the welcome text, three sample questions, disclaimer, question field, answer, and one visible citation. |

## 4. Data flow

### 4.1 Ingestion

1. Load and validate configuration. Reject URLs outside the configured allowlist; disable link discovery and recursive crawling.
2. Fetch each configured page and extract its readable text. Record the canonical source URL, fetch time, and page/update date only when it can be identified reliably.
3. Persist a page record and extracted text in the loading-stage output.
4. Create deterministic chunks that preserve headings and nearby labels with their values. Attach a stable chunk ID, source URL, page title, section/position, and source-date metadata.
5. Persist chunk records in the chunking-stage output.
6. Embed all chunks with the configured sentence-transformers model and persist the vectors and associated chunk IDs/metadata in the embedding-stage output.
7. Store validated vectors in the local ChromaDB staging index and persist its verification manifest.
8. Run `scripts/migrate_qdrant.py` to validate the manifests, source allowlist, chunk/vector alignment, dimensions, and normalized vectors, then upsert deterministic point IDs and source metadata into Qdrant.
9. Report migration failures explicitly; rerunning the migration upserts the same point IDs.

Illustrative local artifact layout:

```text
data/
  loaded/
    pages.jsonl
  chunks/
    chunks.jsonl
  embeddings/
    vectors.npy
    manifest.json
  chroma/
  manifests/
    latest.json
```

The exact file formats may follow the implementation's libraries, but each processing stage must remain independently inspectable. Do not write API keys or user-submitted questions into these artifacts.

### 4.2 Question and answer

1. The UI sends a question to the application without a user identity or account context.
2. The query guard checks for advice/performance intent and sensitive identifiers. Advice and portfolio requests receive a fixed polite refusal with one relevant educational link where available. Performance questions receive a no-calculation response and an approved factsheet link only if configured.
3. For eligible factual questions, normalize the query without changing its meaning and embed it with the same model/version used for the indexed corpus.
4. Embed the query with the corpus model and query Qdrant for the top relevant chunks, optionally filtering to a recognized scheme. Convert Qdrant cosine scores to cosine distances and apply the configured threshold. If retrieval is empty or below threshold, use a transparent “not found in available sources” answer rather than calling the LLM with unsupported context.
5. Send the question and retrieved excerpts to the configured LLM adapter. The prompt requires an answer of no more than three sentences, prohibits unsupported facts and advice, and asks for no link generation.
6. Validate the generated text. Select one citation from the retrieved source metadata in application code, rather than trusting a model-generated URL. Attach `Last updated from sources: ` and the best available verified source date/update metadata; if none exists, explicitly state that a reliable date is unavailable.
7. If the response cannot be validated or the provider fails, return a clear error or safe factual fallback with an applicable source link; never present a failed generation as a successful answer.
8. Render the answer and exactly one clear source link.

## 5. Data model

### Page record

| Field | Description |
|---|---|
| `source_id` | Stable identifier derived from the canonical configured URL. |
| `url` | Canonical public page URL; used for the answer citation. |
| `title` | Extracted page title when available. |
| `content` | Extracted readable page text. |
| `fetched_at` | Timestamp when this version was retrieved. |
| `source_updated_at` | Verifiable date from the page, if present; otherwise null. |
| `content_hash` | Hash of normalized extracted content for change detection. |
| `status` | Fetch/parse outcome for this source. |

### Chunk record

| Field | Description |
|---|---|
| `chunk_id` | Stable identifier based on source, location, and content. |
| `source_id` | Parent page identifier. |
| `url` | Source citation URL. |
| `title` | Parent page title. |
| `text` | Chunk text used for embedding and generation context. |
| `section` | Heading or section label when available. |
| `position` | Page-relative sequence or location. |
| `content_hash` | Chunk fingerprint for idempotent indexing. |
| `source_updated_at` | Verifiable page date, if available. |

The same chunk metadata accompanies its embedding as a Qdrant point payload; ChromaDB retains a local staging copy. API credentials and user question history are not part of either record.

## 6. Retrieval and answer policy

- Use the configured embedding model and Qdrant collection consistently for ingestion and queries. A model change requires re-embedding and rebuilding or versioning the collection.
- Retrieve a small configurable number of chunks. Prefer evidence that directly answers the requested fact and keep chunks from unrelated schemes separate.
- Apply a relevance threshold; a nearest-neighbor result alone does not establish that the answer is supported.
- Keep each answer within three sentences, including its freshness text and citation presentation where sentence counting applies.
- The application, not the LLM, owns citation URLs and freshness metadata. Every normal answer must display exactly one source link.
- If retrieved evidence conflicts or is insufficient, disclose that limitation rather than choosing an unsupported value.
- Do not calculate or compare returns. Route performance requests to an approved factsheet only when one is available in configured sources.
- Refusal responses should be concise and include one relevant educational link when available.

## 7. Privacy and security controls

- Use a strict source allowlist and do not follow links found on source pages.
- Keep `.env` out of version control; fail clearly when the selected provider's required key is missing.
- Avoid request-body, prompt, and query logging. Log operational status and error details without sensitive user input.
- Screen for PAN, Aadhaar, account numbers, OTPs, email addresses, and phone numbers before any model or persistence call. Do not echo detected identifiers; return a short privacy notice instead.
- Do not ask for or persist PII. Keep corpus artifacts limited to public-source content and its metadata.
- Escape rendered text and treat fetched content as untrusted input. Retrieved text is evidence, not instructions; prompts must tell the model to ignore instructions embedded in source content.
- Do not expose provider keys, local artifact paths, or raw exception traces in the UI.

## 8. Configuration and deployment

Use a checked-in, non-secret configuration file for operational settings and environment variables for secrets. Illustrative settings:

```yaml
sources:
  - https://groww.in/mutual-funds/amc/hdfc-mutual-funds
  - https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth
  - https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth
  - https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth
  - https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth
embedding:
  model: sentence-transformers/all-MiniLM-L6-v2
vector_store:
  provider: qdrant
  url_env: QDRANT_URL
  api_key_env: QDRANT_API_KEY
  collection_env: QDRANT_COLLECTION
  collection: hdfc-mutual-funds
local_index:
  provider: chromadb
  path: data/chroma
  collection: hdfc-mutual-funds
  distance_metric: cosine
llm:
  provider: groq
  model_env: LLM_MODEL
  api_key_env: GROQ_API_KEY
retrieval:
  top_k: 4
  minimum_relevance: 0.55
```

The relevance cutoff is a cosine distance; Qdrant's similarity score is converted to distance before applying it. Load the requested initial model identifier, `qwen/qwen3.8-27b`, from `LLM_MODEL` rather than hard-coding it. Provide a local `.env.example` with variable names and placeholder values only.

Run ingestion and migration as explicit commands. Streamlit Community Cloud and Vercel connect to the same Qdrant collection using host-side secrets; the deployed Streamlit app does not depend on the local Chroma staging index.

## 9. Failure handling and observability

- A source fetch or parse failure is recorded per URL; a run with missing pages is reported as partial and must not silently overwrite a previously good index with incomplete data.
- Embedding, local staging, Qdrant migration, or Qdrant query failures stop the affected operation and are reported with actionable, non-secret details.
- A missing provider key, unsupported provider/model, or LLM timeout is surfaced explicitly; do not return a fabricated or success-shaped answer.
- Track operational counts and durations for loaded pages, chunks, embedded records, indexed records, and retrieval results. Do not log full questions or sensitive identifiers.
- Make the current corpus manifest and processing-stage artifacts available for the demonstration.

## 10. Verification strategy

- Unit tests: URL allowlist/no-follow behavior, extraction metadata, deterministic chunk IDs, privacy screening, advice/performance intent handling, citation selection, sentence limits, and freshness formatting.
- Pipeline tests: run loading → chunking → embedding → local Chroma staging and mock Qdrant migration; verify stage artifacts, payloads, and idempotent reruns.
- Retrieval tests: confirm known factual questions retrieve the appropriate scheme/source, while unrelated or unsupported queries trigger the no-evidence behavior.
- UI checks: welcome line, three examples, disclaimer, one visible citation, and refusal behavior.
- Provider tests: exercise the configured adapter with mocked responses and verify missing-key/provider failures are explicit. Avoid requiring live credentials in routine tests.

## 11. Decisions and unresolved points

- **Source count:** The PRD requests a 15–25 URL source list while limiting automated crawling to five pages. Keep ingestion restricted to the five URLs until scope is clarified. Additional public educational or citation URLs must be explicitly approved and added to configuration; do not discover them by crawling.
- **Statement-download instructions:** Answer only if an approved public source documents the workflow. The initial five pages may not cover it.
- **Freshness:** Prefer a verifiable source-provided update date. Otherwise show retrieval metadata as such and explicitly indicate that the source's update date is unavailable.
- **Framework choice:** The PRD does not prescribe a programming language or UI framework. Select a lightweight stack during implementation and keep the component boundaries and observable pipeline stages described here.
- **Provider breadth:** Groq is the initial provider. Other named providers remain extension points until implemented and tested.
