"""Streamlit UI for the facts-only mutual-fund FAQ assistant."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from src.retrieval.answer import AnswerError, AnswerResponse, answer_question


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "app.yaml"
EXAMPLE_QUESTIONS = (
    "What is the expense ratio of HDFC Large Cap Fund?",
    "What is the lock-in period for HDFC ELSS Tax Saver Fund?",
    "What is the minimum SIP for HDFC Mid Cap Fund?",
)


def _read_manifest(name: str) -> dict[str, Any]:
    path = ROOT / "data" / "manifests" / name
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return content if isinstance(content, dict) else {}


def _corpus_status() -> tuple[str, str]:
    vector_manifest = _read_manifest("vector-store-current.json")
    load_manifest = _read_manifest("load-latest.json")
    chunk_manifest = _read_manifest("chunk-latest.json")
    if (
        vector_manifest.get("status") != "success"
        or load_manifest.get("status") != "success"
        or chunk_manifest.get("status") != "success"
    ):
        return "Corpus status unavailable", "The indexed corpus could not be verified."
    source_count = load_manifest.get("loaded_sources", 0)
    chunk_count = vector_manifest.get("stored_count", 0)
    return (
        f"{source_count} public sources · {chunk_count} searchable chunks",
        "Index verified",
    )


def _render_response(response: AnswerResponse) -> None:
    if response.status == "answered":
        st.markdown(
            '<div class="response-label">ANSWER FROM YOUR SOURCES</div>',
            unsafe_allow_html=True,
        )
        st.text(response.answer)
    elif response.status == "refused":
        st.info(response.answer, icon="🛡️")
    elif response.status == "privacy_notice":
        st.warning(response.answer, icon="🔒")
    elif response.status == "no_evidence":
        st.info(response.answer, icon="🔎")
    else:
        st.error("The assistant returned an unexpected response. Please try again.")
        return

    st.caption(response.last_updated_from_sources)
    st.link_button(
        "Open source page",
        response.citation_url,
        type="secondary",
        use_container_width=False,
    )


def main() -> None:
    st.set_page_config(
        page_title="Mutual Fund Facts | FAQ Assistant",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(
        """
        <style>
        :root {
            --canvas: #131313;
            --surface: #1c1b1b;
            --surface-raised: #201f1f;
            --surface-high: #2a2a2a;
            --ink: #e5e2e1;
            --muted: #bacac1;
            --muted-dim: #85948c;
            --green: #44edb7;
            --green-dark: #00d09c;
            --line: #3c4a43;
        }
        .stApp {
            background:
                radial-gradient(ellipse at 50% 4%, rgba(68, 237, 183, .09), transparent 36rem),
                var(--canvas);
            color: var(--ink);
        }
        [data-testid="stHeader"] {
            background: rgba(19, 19, 19, .9);
        }
        [data-testid="stToolbar"] { visibility: hidden; }
        .block-container {
            max-width: 1240px;
            padding: 1.4rem 2rem 3.5rem;
        }
        .topbar {
            display: flex;
            align-items: center;
            gap: .65rem;
            margin-bottom: 2.1rem;
            padding: .55rem 0 1rem;
            border-bottom: 1px solid rgba(60, 74, 67, .6);
            color: var(--ink);
            font-size: 1rem;
            font-weight: 700;
            letter-spacing: -.02em;
        }
        .brand-mark {
            display: grid;
            width: 2.15rem;
            height: 2.15rem;
            place-items: center;
            border: 1px solid rgba(68, 237, 183, .18);
            border-radius: .65rem;
            background: var(--surface-high);
            color: var(--green);
            font-size: 1.15rem;
        }
        .brand-tag {
            margin-left: .5rem;
            padding: .42rem .65rem;
            border-radius: .4rem;
            background: var(--surface-high);
            color: var(--muted);
            font-size: .68rem;
            font-weight: 700;
            letter-spacing: .08em;
            text-transform: uppercase;
        }
        .eyebrow {
            margin-top: .2rem;
            color: var(--green);
            font-size: .76rem;
            font-weight: 700;
            letter-spacing: .13em;
            text-transform: uppercase;
            text-align: center;
        }
        .hero-title {
            max-width: 1100px;
            margin: .7rem auto .85rem;
            color: var(--ink);
            font-size: clamp(2.15rem, 5vw, 3.5rem);
            font-weight: 700;
            letter-spacing: -.045em;
            line-height: 1.12;
            text-align: center;
        }
        .hero-copy {
            max-width: 52rem;
            margin: 0 auto 1.4rem;
            color: var(--muted);
            font-size: 1rem;
            line-height: 1.7;
            text-align: center;
        }
        .status-pill {
            display: flex;
            width: fit-content;
            align-items: center;
            gap: .55rem;
            margin: .35rem auto 1.3rem;
            padding: .5rem .85rem;
            border: 1px solid var(--line);
            border-radius: 999px;
            background: var(--surface);
            color: var(--muted);
            font-size: .72rem;
            font-weight: 650;
            letter-spacing: .025em;
        }
        .dot {
            width: .5rem;
            height: .5rem;
            border-radius: 50%;
            background: var(--green);
            box-shadow: 0 0 12px rgba(68, 237, 183, .45);
        }
        .question-heading {
            margin: 1.6rem 0 .65rem;
            color: var(--muted);
            font-size: .8rem;
            font-weight: 700;
            letter-spacing: .07em;
            text-transform: uppercase;
        }
        div[data-testid="stForm"] {
            padding: .9rem;
            border: 1px solid rgba(60, 74, 67, .55);
            border-radius: 1rem;
            background: var(--surface);
            box-shadow: 0 14px 42px rgba(0, 0, 0, .22);
        }
        div[data-testid="stForm"] input {
            min-height: 3.3rem;
            border: 1px solid transparent;
            border-radius: .7rem;
            background: #0e0e0e;
            color: var(--ink);
            font-size: .96rem;
        }
        div[data-testid="stForm"] input::placeholder {
            color: var(--muted-dim);
        }
        div[data-testid="stForm"] button[data-testid="stBaseButton-primaryFormSubmit"] {
            min-height: 3.3rem;
            border: 1px solid transparent;
            border-radius: .7rem;
            background: var(--green);
            color: #003828;
            font-weight: 750;
        }
        div[data-testid="stForm"] button[data-testid="stBaseButton-primaryFormSubmit"]:hover {
            border-color: var(--green);
            background: var(--green-dark);
            color: #003828;
        }
        input:focus, textarea:focus, button:focus-visible, a:focus-visible {
            outline: 2px solid var(--green) !important;
            outline-offset: 2px;
        }
        .facts-note {
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            margin: .45rem .25rem 1rem;
            color: var(--muted-dim);
            font-size: .78rem;
            line-height: 1.5;
        }
        .facts-note strong {
            color: var(--muted);
        }
        .examples-title {
            margin: 1.8rem 0 .7rem;
            color: var(--muted-dim);
            font-size: .74rem;
            font-weight: 700;
            letter-spacing: .1em;
            text-transform: uppercase;
        }
        div[data-testid="stButton"] button[data-testid="stBaseButton-secondary"] {
            min-height: 8.5rem;
            border-color: var(--line);
            border-radius: .85rem;
            background: linear-gradient(145deg, #201f1f, #1a1a1a);
            color: var(--ink);
            text-align: left;
            white-space: normal;
            transition: border-color .16s ease, transform .16s ease, background .16s ease;
        }
        div[data-testid="stButton"] button[data-testid="stBaseButton-secondary"] .stMarkdownEllipsis {
            width: 100%;
            overflow: visible !important;
            text-overflow: clip !important;
            white-space: normal !important;
        }
        div[data-testid="stButton"] button[data-testid="stBaseButton-secondary"] .stMarkdownEllipsis p {
            margin: 0;
            white-space: normal !important;
            line-height: 1.5;
        }
        div[data-testid="stButton"] button[data-testid="stBaseButton-secondary"]:hover {
            border-color: rgba(68, 237, 183, .65);
            background: var(--surface-high);
            color: var(--ink);
            transform: translateY(-2px);
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
            margin-top: 1.4rem;
            border-color: var(--line);
            border-radius: 1rem;
            background: var(--surface);
            box-shadow: 0 12px 32px rgba(0, 0, 0, .18);
        }
        .response-label {
            margin: .1rem 0 .55rem;
            color: var(--green);
            font-size: .72rem;
            font-weight: 700;
            letter-spacing: .11em;
        }
        [data-testid="stAlert"] {
            border: 1px solid var(--line);
            border-radius: .8rem;
            background: var(--surface-raised);
            color: var(--ink);
        }
        [data-testid="stAlertContainer"] {
            border-color: var(--line) !important;
            background: var(--surface-high) !important;
            color: var(--muted) !important;
        }
        [data-testid^="stAlertContent"] {
            color: var(--muted) !important;
        }
        [data-testid="stAlertContainer"] svg {
            color: var(--green) !important;
        }
        [data-testid="stLinkButton"] a {
            border-color: var(--line);
            border-radius: .65rem;
            color: var(--green);
        }
        [data-testid="stLinkButton"] a:hover {
            border-color: var(--green);
            background: var(--surface-high);
            color: var(--green);
        }
        .footer-note {
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            margin-top: 2.7rem;
            padding: 1.2rem 0 .2rem;
            border-top: 1px solid rgba(60, 74, 67, .6);
            color: var(--muted-dim);
            font-size: .76rem;
            line-height: 1.65;
        }
        @media (max-width: 640px) {
            .block-container { padding: 1rem 1rem 2.5rem; }
            .topbar { margin-bottom: 1.5rem; }
            .brand-tag { display: none; }
            .hero-title { font-size: clamp(2rem, 10vw, 2.8rem); }
            .hero-copy { font-size: .92rem; }
            .status-pill { font-size: .66rem; }
            .facts-note, .footer-note { display: block; }
            div[data-testid="stForm"] { padding: .65rem; }
            div[data-testid="stButton"] button { min-height: 4.5rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    corpus_detail, corpus_status = _corpus_status()
    st.markdown(
        """
        <div class="topbar"><span class="brand-mark">▦</span>
        <span>Mutual Fund Facts</span>
        <span class="brand-tag">HDFC MF · Facts-only reference</span></div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="status-pill"><span class="dot"></span>{corpus_status} · {corpus_detail}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="eyebrow">HDFC Mutual Fund · Facts-only assistant</div>
        <h1 class="hero-title">A clearer answer to your fund questions.</h1>
        <p class="hero-copy">Ask about scheme details like expense ratio, exit load, minimum SIP, riskometer, benchmark, or ELSS lock-in. Answers are grounded in the listed public pages.</p>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="question-heading">What would you like to know?</div>', unsafe_allow_html=True)
    with st.form("faq_question_form", clear_on_submit=True):
        input_column, submit_column = st.columns([4, 1], vertical_alignment="center")
        with input_column:
            question = st.text_input(
                "Ask a question",
                placeholder="e.g. What is the exit load for HDFC Flexi Cap Fund?",
                label_visibility="collapsed",
                max_chars=500,
                key="question_input",
            )
        with submit_column:
            submitted = st.form_submit_button(
                "Get a factual answer",
                type="primary",
                use_container_width=True,
            )
    st.markdown(
        '<div class="facts-note"><strong>Facts-only. No investment advice.</strong> '
        "Please don't enter PAN, Aadhaar, account numbers, OTPs, email, or phone numbers.</div>",
        unsafe_allow_html=True,
    )

    st.markdown('<div class="examples-title">Try an example</div>', unsafe_allow_html=True)
    example_columns = st.columns(3)
    selected_question: str | None = None
    for index, (column, example) in enumerate(zip(example_columns, EXAMPLE_QUESTIONS)):
        with column:
            if st.button(example, key=f"example_{index}", use_container_width=True):
                selected_question = example

    question_to_answer = selected_question or (question if submitted else "")
    if submitted and not question.strip():
        st.warning("Enter a question or choose an example to get started.")

    if question_to_answer:
        try:
            with st.spinner("Checking the approved sources…"):
                response = answer_question(question_to_answer, CONFIG_PATH)
            with st.container(border=True):
                _render_response(response)
        except AnswerError:
            st.info("The assistant couldn't complete that request. Please try again shortly.")

    st.markdown(
        """
        <div class="footer-note">
        Answers use a small set of public HDFC Mutual Fund pages. Each response includes
        one source link. Information may change; verify important details with the source.
        Facts-only · No investment advice.
        </div>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
