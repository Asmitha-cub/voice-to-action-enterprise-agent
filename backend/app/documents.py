"""LLM-backed document generation (leave letters, memos, emails, notices, ...).

This is what lets the agent do more than a few fixed operations: it can *author*
content on request. If no LLM key is configured it falls back to a clean
template so the feature still works in a demo.
"""
from __future__ import annotations

import html
import logging

from openai import OpenAI

from .config import settings

log = logging.getLogger(__name__)

_SYSTEM = (
    'You are an enterprise writing assistant. Write a complete, professional '
    '{doc_type} ready to send. Use a clear greeting and sign-off. Return plain '
    'text only — no markdown, no commentary, just the document body.'
)


def _html_wrap(text: str) -> str:
    return (
        "<div style=\"font-family:Segoe UI,Arial,sans-serif;color:#0f172a;white-space:pre-wrap\">"
        + html.escape(text)
        + "</div>"
    )


def _fallback_document(doc_type: str, instructions: str, context: dict) -> dict:
    recipient = context.get('recipient_name', '') or 'Sir/Madam'
    sender = context.get('sender_name', '') or 'Enterprise Agent'
    body = (
        f'Dear {recipient},\n\n'
        f'{instructions or ("Please find below the " + doc_type + ".")}\n\n'
        f'Sincerely,\n{sender}'
    )
    return {'title': doc_type.strip().title(), 'body_text': body,
            'body_html': _html_wrap(body), 'generated_by': 'template'}


def generate_document(doc_type: str, instructions: str = '', context: dict | None = None) -> dict:
    """Return {title, body_text, body_html, generated_by}. Never raises."""
    context = context or {}
    doc_type = (doc_type or 'letter').strip() or 'letter'
    cfg = settings()
    if not cfg.groq_api_key:
        return _fallback_document(doc_type, instructions, context)
    try:
        client = OpenAI(api_key=cfg.groq_api_key, base_url=cfg.groq_base_url,
                        timeout=cfg.llm_timeout_seconds, max_retries=0)
        user_prompt = (
            f'Write a {doc_type}.\n'
            f'Details/instructions: {instructions or "(none given)"}\n'
            f'Recipient name: {context.get("recipient_name", "") or "(unspecified)"}\n'
            f'Sender name: {context.get("sender_name", "") or "(unspecified)"}'
        )
        resp = client.chat.completions.create(
            model=cfg.llm_model,
            temperature=0.3,
            messages=[
                {'role': 'system', 'content': _SYSTEM.format(doc_type=doc_type)},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        text = (resp.choices[0].message.content or '').strip()
        if not text:
            return _fallback_document(doc_type, instructions, context)
        return {'title': doc_type.title(), 'body_text': text,
                'body_html': _html_wrap(text), 'generated_by': 'llm'}
    except Exception as exc:  # noqa: BLE001
        log.warning('Document generation failed; using template: %s', exc)
        return _fallback_document(doc_type, instructions, context)
