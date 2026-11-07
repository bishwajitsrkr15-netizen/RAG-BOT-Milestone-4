# Stitch UI Generation Prompt

Paste the prompt below into Stitch to generate UI screens for the Mutual Fund Facts assistant.

---

Design a polished, responsive web UI for **Mutual Fund Facts**, a facts-only FAQ assistant about HDFC Mutual Fund and five approved public scheme pages. This is a reference-information tool, not an investment-advice or portfolio app.

## Brand and visual direction

- Follow the **Obsidian Emerald Financial** theme in `stitch_growth_dark_mode_theme (1).zip`.
- Use a near-black canvas (`#131313`), charcoal cards (`#1c1b1b`, `#201f1f`), warm-white text (`#e5e2e1`), muted sage secondary text (`#bacac1`), and mint-green accents (`#44edb7`).
- Use subtle charcoal borders (`#3c4a43`), rounded panels, restrained shadows, and a soft emerald ambient glow.
- Use clear, spacious typography; high contrast, visible focus states, and accessible controls.
- Responsive layouts for desktop and mobile.
- Do not use Groww logos, imitate proprietary branding, or show charts, return figures, fund rankings, or buy/sell buttons.
- Do not add mock corpus counts, fund metrics, navigation destinations, or operational claims unless the application supplies verified data for them.

## Required screens

### 1. Welcome / empty state

- Compact header brand mark and title: “Mutual Fund Facts”.
- Eyebrow: “HDFC Mutual Fund · Facts-only assistant”.
- Main welcome heading: “A clearer answer to your fund questions.”
- Supporting text explaining users can ask about expense ratio, exit load, minimum SIP, riskometer, benchmark, ELSS lock-in, and statement instructions where sources cover them.
- Small corpus status pill with source/chunk count and verified-index status.
- Question composer with a helpful placeholder and a prominent “Get a factual answer” button.
- Visible note: **“Facts-only. No investment advice.”**
- Additional privacy hint: do not enter PAN, Aadhaar, account numbers, OTPs, email, or phone numbers.
- Exactly three clickable example questions:
  1. “What is the expense ratio of HDFC Large Cap Fund?”
  2. “What is the lock-in period for HDFC ELSS Tax Saver Fund?”
  3. “What is the minimum SIP for HDFC Mid Cap Fund?”

### 2. Answer state

- Keep the question composer and examples available.
- Show one distinct answer card labelled “Answer from your sources”.
- Render a concise answer (maximum three sentences).
- Include the exact freshness label “Last updated from sources: …”.
- Show exactly one source-link control in the answer card, clearly labelled “Open source page”.
- Include an unobtrusive loading state while evidence and answer are being retrieved.

### 3. Refusal and no-evidence states

- **Advice / portfolio question:** polite facts-only refusal, no recommendation; include one relevant educational/source link when available.
- **Performance / returns question:** explain that returns will not be calculated or compared; point to a factsheet only if one is configured.
- **No supporting evidence:** say the fact was not found in the available sources and avoid guessing; show one relevant approved source link if available.
- Use calm informational callouts, not alarming red error treatments.

### 4. Privacy and service-error states

- **Sensitive personal/account details detected:** show a brief privacy notice without repeating the value; make clear the value was not processed or stored.
- **Service/provider failure:** show a generic retry message without keys, stack traces, raw exceptions, or local file paths.
- Do not include an account/profile area, chat history, sign-in flow, or data-entry fields beyond the question composer.

## Interaction and content rules

- Every answer/refusal/no-evidence result has no more than three sentences, exactly one visible source link, and the freshness label.
- Source links are public URLs selected by the application; do not make up URLs or citations.
- No personal data collection, financial recommendations, performance comparisons, investment-return numbers, or buy/sell calls.
- Keep each state visually consistent with the welcome screen.
- Use semantic headings, visible keyboard focus, accessible labels, and clear hover/pressed states.

Generate a small coherent set of screen designs for desktop and mobile that covers the welcome state, grounded answer, polite refusal/no evidence, privacy notice, and service error. Prefer a single-page application layout.
