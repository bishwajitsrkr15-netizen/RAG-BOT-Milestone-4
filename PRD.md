# Product Requirements Document: Mutual Fund FAQ Assistant

## 1. Product summary

Build a small, facts-only FAQ assistant for factual questions about HDFC Mutual Fund and a limited set of HDFC schemes. The prototype uses retrieval-augmented generation (RAG) over a small, traceable corpus of public pages. Every answer must cite a source, stay concise, and avoid investment advice.

The ingestion and retrieval pipeline must be implemented in visible phases so the demo can show the data produced at each stage: loading, chunking, embedding, and vector storage.

## 2. Problem and users

Retail users comparing schemes and support/content teams repeatedly need basic scheme facts, such as expense ratio, exit load, minimum SIP, ELSS lock-in, riskometer, benchmark, and statement-download instructions. The assistant should make these facts easier to find without presenting opinions or personalized recommendations.

### Intended users

- Retail users looking for factual information about the listed AMC and schemes.
- Support and content teams answering repetitive mutual-fund questions.

## 3. Goals and non-goals

### Goals

- Answer supported factual questions using retrieved public-source content.
- Include one clear source link in every answer.
- Make each RAG processing stage and its stored output inspectable.
- Refuse investment-advice and portfolio-opinion requests politely, with a relevant educational link where available.
- Keep the prototype small, configurable, and demonstrable.

### Non-goals

- Recommending, ranking, or advising users to buy, sell, or hold investments.
- Computing, comparing, or making claims about investment performance or returns.
- Collecting or storing personal or account information.
- Crawling links found within the specified pages.
- Building a production-grade financial service or account-integrated statement-download flow.

## 4. Initial corpus and source policy

### Specified pages

The initial crawl is limited to these five pages. Do not follow or scrape links found on these pages.

| Page | URL |
|---|---|
| HDFC Mutual Fund AMC overview | https://groww.in/mutual-funds/amc/hdfc-mutual-funds |
| HDFC Large Cap Fund Direct Growth | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| HDFC Flexi Cap Direct Plan Growth | https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth |
| HDFC ELSS Tax Saver Fund Direct Plan Growth | https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth |
| HDFC Mid Cap Fund Direct Growth | https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth |

### Source rules

- Use public pages only. Do not use app-backend screenshots or third-party blogs.
- Retain the source URL and page/update metadata with extracted content so citations and freshness can be shown.
- Do not crawl internal links from the five specified pages.
- If an answer cannot be supported by retrieved source content, say that the information was not found in the available sources rather than guessing.
- If asked about performance, do not calculate or compare returns; direct the user to an appropriate public factsheet if one is available in the approved corpus.

## 5. Functional requirements

### 5.1 Data ingestion and RAG stages

Implement the pipeline in the following phases, with intermediate data stored explicitly for inspection:

1. **Data loading:** Fetch and parse only the configured source URLs. Store the extracted page content together with its URL and available retrieval/update metadata.
2. **Data chunking:** Choose a chunking strategy suited to the page structure and facts. Store the chunks and enough source metadata to trace each chunk back to its page and location.
3. **Data embedding:** Embed each chunk using the configured embedding model. The initial model is `sentence-transformers/all-MiniLM-L6-v2`. Make the model configurable.
4. **Vector storage:** Store embeddings and associated chunk/source metadata in ChromaDB. Make the vector database configurable.
5. **Retrieval and answer generation:** Embed the query using the same embedding model used at ingestion, retrieve relevant chunks from the configured vector database, and generate a concise, cited response using the configured LLM provider/model.

Each phase must be runnable and inspectable independently enough to demonstrate its input and output. Re-running ingestion should not silently leave stale or duplicate corpus entries.

### 5.2 Supported question types

The assistant should support factual questions where the answer is present in the available sources, including:

- Expense ratio.
- Exit load.
- Minimum SIP.
- ELSS lock-in period.
- Riskometer and benchmark.
- How to download a capital-gains statement, when covered by an approved public source.
- Other basic scheme or AMC facts present in retrieved content.

### 5.3 Answer behavior

- Answers must contain no more than three sentences.
- Every answer must include exactly one clear source link supporting the answer.
- Include the text `Last updated from sources: ` followed by the available source date/metadata. If no reliable source date is available, state that it is unavailable; do not imply a verified update date.
- Use only facts supported by retrieved content. Do not invent missing values or combine unrelated facts.
- When the answer is unavailable, clearly say so and provide one relevant source link when possible.
- Refuse opinionated, personalized, or portfolio questions (for example, “Should I buy/sell?”) with a polite facts-only response and one relevant educational source link where available.
- Do not calculate, compare, or claim returns or performance. For performance requests, direct users to an approved factsheet link if available.

### 5.4 User interface

Provide a tiny interface containing:

- A welcome line.
- Three example questions.
- The note: **“Facts-only. No investment advice.”**
- A question input and a clearly readable answer with its source link and source freshness text.
- A polite refusal state for advice or portfolio-opinion requests.

### 5.5 Configuration and model providers

Keep ingestion and retrieval settings in a configuration file so they can be changed without modifying pipeline logic. At minimum configure:

- Source URLs.
- Embedding model.
- Vector database.
- LLM provider and model.

The initial vector database is ChromaDB. The initial embedding model is `sentence-transformers/all-MiniLM-L6-v2`.

The initial LLM provider is Groq. Read its API key and model selection from environment variables in a local `.env` file; do not commit credentials. The requested initial model identifier is `qwen/qwen3.8-27b` and must be treated as configurable.

Keep the provider configuration extensible for Gemini, OpenAI, Anthropic, and Mistral. A provider should only be presented as supported when its configuration and request path are implemented; provider names alone do not imply a working integration.

## 6. Privacy, safety, and transparency

- Do not accept, store, log, or send PAN, Aadhaar, account numbers, OTPs, email addresses, or phone numbers to the model or retrieval service.
- Do not ask for personal or account information. If a user supplies it, do not repeat it in the answer or persist it.
- Do not provide investment advice, suitability assessments, or personalized portfolio guidance.
- Show the facts-only disclaimer in the interface.
- Keep responses concise, make citations visible, and be transparent when sources are missing or stale.

## 7. Deliverables

1. A working prototype link (app or notebook), or a demo video of no more than three minutes if hosting is not possible.
2. A source list in CSV or Markdown format covering the 15–25 URLs used.
3. A README with setup steps, corpus scope (AMC and schemes), and known limitations.
4. A sample Q&A file containing 5–10 queries, answers, and source links.
5. The disclaimer snippet used in the UI.
6. Inspectable outputs for the data-loading, chunking, embedding, and vector-storage stages.

## 8. Acceptance criteria

- The five specified pages can be ingested without crawling their linked pages.
- Each pipeline phase stores inspectable outputs, and embedding/vector settings are configurable.
- Retrieval uses the same configured embedding model as ingestion and the configured vector database.
- The prototype answers representative factual questions only when supported by retrieved content.
- Every answer is at most three sentences, includes one clear citation link, and includes the required source freshness text.
- Unsupported questions do not result in fabricated facts.
- Advice and portfolio-opinion questions receive a polite facts-only refusal and a relevant educational link when available.
- Performance questions do not trigger return calculations or comparisons.
- The UI displays the welcome line, three example questions, and the facts-only disclaimer.
- No personal or account identifiers are required or stored.
- The required prototype/demo, source list, README, sample Q&A, and disclaimer deliverables are provided.

## 9. Open decisions and assumptions

- **Source-count versus crawl-scope:** The requirements both restrict crawling to the five listed pages and request a source list of 15–25 URLs. Confirm whether the 15–25 URLs may be manually approved public citation/educational sources (such as AMC, SEBI, or AMFI pages) without crawling them, or whether the permitted corpus itself should expand. Until clarified, keep automated ingestion limited to the five specified URLs and do not claim a 15–25 URL corpus.
- **Statement-download coverage:** The five specified pages may not explain how to download capital-gains statements. Add an answer only if an approved public source covers the workflow.
- **Source freshness:** The exact metadata available on each page may differ. Display a verifiable page date when present; otherwise disclose that a reliable date is unavailable.
- **LLM model identifier:** Preserve `qwen/qwen3.8-27b` as the requested configurable value, and validate it against the provider's available model identifiers during setup.
