"use client";

import { FormEvent, useState } from "react";

import type { AnswerResult } from "@/lib/answer";

const EXAMPLES = [
  "What is the expense ratio of HDFC Large Cap Fund?",
  "What is the lock-in period for HDFC ELSS Tax Saver Fund?",
  "What is the minimum SIP for HDFC Mid Cap Fund?",
];

type ApiResult = AnswerResult | { error: string };

export default function Home() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submitQuestion(value: string) {
    const normalized = value.trim();
    if (!normalized) {
      setError("Enter a question or choose an example to get started.");
      return;
    }
    setQuestion(normalized);
    setError("");
    setResult(null);
    setLoading(true);
    try {
      const response = await fetch("/api/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: normalized }),
      });
      const data = (await response.json()) as ApiResult;
      if (!response.ok || "error" in data) {
        setError(
          "error" in data
            ? data.error
            : "The assistant couldn't complete that request. Please try again.",
        );
        return;
      }
      setResult(data);
    } catch {
      setError("The assistant couldn't complete that request. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitQuestion(question);
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            ▦
          </span>
          <span>Mutual Fund Facts</span>
        </div>
        <span className="brand-tag">HDFC MF · Facts-only reference</span>
      </header>

      <section className="hero" aria-labelledby="page-title">
        <div className="status-pill">
          <span className="dot" />
          Approved public sources · Facts-only assistant
        </div>
        <p className="eyebrow">HDFC Mutual Fund · Facts-only assistant</p>
        <h1 id="page-title">A clearer answer to your fund questions.</h1>
        <p className="hero-copy">
          Ask about scheme details like expense ratio, exit load, minimum SIP,
          riskometer, benchmark, or ELSS lock-in. Answers are grounded in the
          listed public pages.
        </p>
      </section>

      <section className="ask-section" aria-labelledby="question-label">
        <h2 id="question-label">What would you like to know?</h2>
        <form className="question-form" onSubmit={onSubmit}>
          <label className="visually-hidden" htmlFor="question">
            Ask a question
          </label>
          <input
            id="question"
            value={question}
            onChange={(event) => setQuestion(event.target.value.slice(0, 500))}
            placeholder="e.g. What is the exit load for HDFC Flexi Cap Fund?"
            maxLength={500}
            autoComplete="off"
          />
          <button type="submit" disabled={loading}>
            {loading ? "Checking sources…" : "Get a factual answer"}
            <span aria-hidden="true">→</span>
          </button>
        </form>

        <div className="facts-note">
          <span>
            <strong>Facts-only. No investment advice.</strong>
          </span>
          <span>
            Please don&apos;t enter PAN, Aadhaar, account numbers, OTPs, email,
            or phone numbers.
          </span>
        </div>
      </section>

      <section className="examples" aria-labelledby="examples-title">
        <div className="section-heading">
          <h2 id="examples-title">Try an example</h2>
          <span>Suggested questions</span>
        </div>
        <div className="example-grid">
          {EXAMPLES.map((example, index) => (
            <button
              className="example-card"
              key={example}
              onClick={() => void submitQuestion(example)}
              disabled={loading}
              type="button"
            >
              <span className="example-category">
                {["Expense ratio", "ELSS & tax", "SIP limits"][index]}
              </span>
              <span className="example-question">“{example}”</span>
              <span className="example-arrow" aria-hidden="true">
                ↗
              </span>
            </button>
          ))}
        </div>
      </section>

      {loading && (
        <p className="loading-message" role="status">
          Checking the approved sources…
        </p>
      )}
      {error && (
        <div className="result-card callout" role="alert">
          {error}
        </div>
      )}
      {result && (
        <section className="result-card" aria-live="polite">
          <p className="response-label">
            {result.status === "answered"
              ? "ANSWER FROM YOUR SOURCES"
              : result.status === "privacy_notice"
                ? "PRIVACY NOTICE"
                : result.status === "no_evidence"
                  ? "NO SUPPORTING EVIDENCE"
                  : "FACTS-ONLY GUIDANCE"}
          </p>
          <p className="answer-text">{result.answer}</p>
          <p className="freshness">{result.lastUpdatedFromSources}</p>
          <a
            className="source-link"
            href={result.citationUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open source page <span aria-hidden="true">↗</span>
          </a>
        </section>
      )}

      <footer className="footer">
        Answers use a small set of public HDFC Mutual Fund pages. Each response
        includes one source link. Information may change; verify important
        details with the source. Facts-only · No investment advice.
      </footer>
    </main>
  );
}
