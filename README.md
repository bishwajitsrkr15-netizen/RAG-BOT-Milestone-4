# Mutual Fund FAQ Assistant

## Load and scrape the approved source pages

The workflow has two explicit commands. Scraping fetches only the five URLs in `config/app.yaml` and saves raw HTML; loading parses those saved documents into normalized page records. Neither step discovers or fetches links found within the pages.

### Setup

Requires Python 3.10 or later.

```powershell
python -m pip install -r requirements.txt
```

### 1. Scrape approved URLs

Run from the project root:

```powershell
python -m src.ingestion.scrape_documents --config config/app.yaml
```

The command saves raw HTML and fetch metadata under `data/raw/` and writes run status to `data/manifests/scrape-latest.json`.

### 2. Load saved documents

After scraping:

```powershell
python -m src.ingestion.load_documents --config config/app.yaml
```

The loader makes no network requests. It verifies saved document metadata and checksums, extracts readable text into `data/loaded/pages.jsonl`, and writes status to `data/manifests/load-latest.json`. If any source is missing or invalid, the run is marked partial and the previous loaded corpus is left unchanged.

### 3. Chunk loaded documents

After all five pages load successfully:

```powershell
python -m src.ingestion.chunk_documents --config config/app.yaml
```

The chunker is offline and reads `data/loaded/pages.jsonl`. It keeps line and fact boundaries where possible, includes the source page title in each chunk, and enforces a 1,400-character maximum by default. Chunk records with source URL, title, source ID, position, content hash, and stable chunk ID are saved to `data/chunks/chunks.jsonl`; counts are written to `data/manifests/chunk-latest.json`. Change the bound with `--max-chars`.

### 4. Embed chunks

After chunking:

```powershell
python -m src.ingestion.embed_documents --config config/app.yaml
```

The embedding stage reads `data/chunks/chunks.jsonl`, embeds each chunk using the configured model (`sentence-transformers/all-MiniLM-L6-v2`), normalizes the vectors, and saves each vector beside its `chunk_id` in `data/embeddings/vectors.jsonl`. The manifest at `data/manifests/embedding-latest.json` records the model, input hash, batch size, vector count, and dimensions. Override batch size with `--batch-size`.

### 5. Store embeddings in ChromaDB

After embeddings are generated:

```powershell
python -m src.ingestion.store_vectors --config config/app.yaml
```

The storage phase checks the chunk/vector IDs, dimensions, finite normalized values, embedding model, and chunk-file hash before writing. It creates a persistent, versioned cosine-distance collection under `data/chroma/`, storing each vector with its chunk text and citation metadata. Rerunning the same inputs upserts the same IDs into the same collection. Run status is saved to `data/manifests/vector-store-latest.json`; `vector-store-current.json` points to the last successfully verified collection and is not changed by a failed run.

### 6. Retrieve evidence and answer a factual question

Query the verified vector index:

```powershell
python -m src.retrieval.retrieve "What is the expense ratio of HDFC Large Cap Fund?"
```

Generate a concise answer from retrieved evidence:

```powershell
python -m src.retrieval.answer "What is the expense ratio of HDFC Large Cap Fund?"
```

Retrieval uses the configured embedding model, persistent ChromaDB collection, and cosine-distance cutoff (`0.55`). A recognized scheme name filters retrieval to that scheme's source page; weak matches are discarded. The cutoff was checked against representative FAQ phrasings (supported matches up to about `0.53`) and unrelated questions (above `0.82`); recalibrate it if the corpus or embedding model changes.

The answer flow checks sensitive identifiers and advice/performance intent before retrieval or LLM calls. It does not call the LLM when evidence is missing. Groq uses `GROQ_API_KEY` and `LLM_MODEL` from the process environment or local `.env`; model defaults to `qwen/qwen3.8-27b`. The model is instructed not to emit citations; the application attaches one approved source URL from retrieved metadata. Returned fields include answer status, source link, and `Last updated from sources: ...`. A verified page update date is currently unavailable for the ingested pages.

### 7. Run the UI

Install dependencies and launch the Streamlit app from the project root:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run src/ui/app.py
```

The UI presents the welcome message, three example questions, facts-only disclaimer, corpus status, and a single current response. It displays answer, refusal, privacy, no-evidence, and generic service-error states without saving question history or displaying sensitive input.

For UI mockups, paste [`STITCH_UI_PROMPT.md`](./STITCH_UI_PROMPT.md) into Stitch to generate the welcome, answer, refusal, privacy, and error screens in desktop and mobile layouts.

### Deploy the original Streamlit app to Community Cloud

The original Python app remains at `src/ui/app.py`.

1. Push the project to a GitHub repository. Keep `.env` local; `.gitignore` excludes it. Commit the safe `.env.example` template instead.
2. The app needs its persisted Chroma index to answer questions. The Chroma database and the `chunk-latest.json`, `load-latest.json`, and `vector-store-current.json` manifests are included for deployment. Raw downloaded HTML, source records, and standalone embeddings remain excluded.
3. Create a Community Cloud app from the repository and set its main file to `src/ui/app.py`.
4. In the app's **Settings → Secrets**, add the values below, replacing the placeholder with your own key. Do not commit this file or paste the key into chat.

   ```toml
   GROQ_API_KEY = "your_groq_api_key_here"
   LLM_MODEL = "qwen/qwen3.8-27b"
   ```

The answer service reads API settings from the process environment, Streamlit secrets, or a local `.env` in that order. On deployment, use Streamlit secrets rather than uploading `.env`.

The local vector index is a snapshot of the approved public pages at ingestion time. Refresh the index locally and push the updated Chroma database and manifests when the sources change.

### Deploy the Next.js app to Vercel

The repository also includes a Vercel-compatible Next.js UI and same-origin `/api/answer` endpoint. Unlike the Streamlit app, this version uses remote services so it does not depend on writable Vercel filesystem state or a local embedding model.

#### Prepare the hosted vector index

1. Create a Qdrant Cloud cluster and an API key with permission to create collections and upsert points.
2. Set these values in a local PowerShell session (do not commit or paste secrets):

   ```powershell
   $env:QDRANT_URL = "https://<your-cluster>.qdrant.io"
   $env:QDRANT_API_KEY = "<your-qdrant-api-key>"
   $env:QDRANT_COLLECTION = "hdfc-mutual-funds"
   ```

3. Run the migration from the project root. It validates the local manifests, chunk hash, source allowlist, 384-dimensional vectors, and chunk/vector IDs, then uploads the 76 points:

   ```powershell
   python scripts/migrate_qdrant.py
   ```

The migration is idempotent and uses deterministic point IDs; rerunning it upserts the same points. Keep the local `data/chunks` and `data/embeddings` artifacts for later corpus refreshes.

#### Connect Vercel

1. Import the GitHub repository into Vercel. Use the repository root as the **Root Directory** and the **Next.js** framework preset. Vercel detects the root `package.json`; use `npm run build` as the build command and leave output settings at their defaults.
2. In **Project Settings → Environment Variables**, add:
   - `GROQ_API_KEY` — secret; Groq API key.
   - `LLM_MODEL` — `qwen/qwen3.8-27b`.
   - `HF_TOKEN` — secret; Hugging Face token with Inference Providers permission.
   - `QDRANT_URL` — the HTTPS URL for the Qdrant cluster.
   - `QDRANT_API_KEY` — secret; Qdrant API key.
   - `QDRANT_COLLECTION` — `hdfc-mutual-funds`.
3. Apply the variables to Preview and Production, save, and deploy. If variables are changed later, create a new deployment.
4. Test a supported question, an unrelated question, and an advice question in the deployed UI.

The Vercel API applies the same privacy, advice, performance, approved-source citation, and cosine-distance guardrails. It uses Hugging Face Inference for query vectors and Qdrant Cloud for retrieval. The local Streamlit app continues to use its local ChromaDB index and can be deployed separately to Community Cloud.

Before sharing the Vercel URL publicly, configure a Vercel Firewall rate limit for `POST /api/answer`; the public endpoint calls metered embedding and LLM providers.

## Current limits

- The scraper is limited to the five HDFC Mutual Fund pages listed in the PRD.
- No linked pages, other Groww pages, or third-party sources are fetched.
- A source update date is recorded only when the page exposes verifiable metadata; the fetch timestamp is stored separately.
- Stitch prompts describe mockup requirements; generated Stitch screens are design references, not wired application code.

## Tests

Run the scraper unit tests with:

```powershell
python -m unittest discover -s tests -v
```
