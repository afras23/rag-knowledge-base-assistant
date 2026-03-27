"""
Streamlit chat UI for the RAG Knowledge Base Assistant (Phase 12).

Run from repo root::

    RAG_API_BASE=http://localhost:8000/api/v1 streamlit run frontend/app.py

Environment:
    RAG_API_BASE — FastAPI prefix including ``/api/v1``
    (default ``http://localhost:8000/api/v1``).
"""

from __future__ import annotations

import os
from typing import Any, cast

import httpx
import streamlit as st

DEFAULT_API_BASE = "http://localhost:8000/api/v1"


def _api_base() -> str:
    return os.environ.get("RAG_API_BASE", DEFAULT_API_BASE).rstrip("/")


def _get_json(client: httpx.Client, path: str) -> dict[str, Any]:
    """GET JSON and return the ``data`` field from a success envelope."""
    response = client.get(f"{_api_base()}{path}", timeout=60.0)
    response.raise_for_status()
    payload = cast(dict[str, Any], response.json())
    if payload.get("status") != "success":
        err = payload.get("error", {})
        msg = err.get("message", str(payload)) if isinstance(err, dict) else str(err)
        raise RuntimeError(msg)
    return cast(dict[str, Any], payload["data"])


def _post_query(
    client: httpx.Client,
    *,
    question: str,
    conversation_id: str | None,
) -> dict[str, Any]:
    """POST ``/chat/query`` and return the ``data`` object."""
    body: dict[str, Any] = {"question": question, "max_chunks": 8}
    if conversation_id:
        body["conversation_id"] = conversation_id
    response = client.post(
        f"{_api_base()}/chat/query",
        json=body,
        timeout=120.0,
    )
    response.raise_for_status()
    payload = cast(dict[str, Any], response.json())
    if payload.get("status") != "success":
        err = payload.get("error", {})
        msg = err.get("message", str(payload)) if isinstance(err, dict) else str(err)
        raise RuntimeError(msg)
    return cast(dict[str, Any], payload["data"])


def _list_conversations(client: httpx.Client, *, page_size: int = 50) -> dict[str, Any]:
    return _get_json(client, f"/chat/conversations?page=1&page_size={page_size}")


def _format_citation(citation: dict[str, Any]) -> str:
    title = str(citation.get("document_title", "Document"))
    section = str(citation.get("page_or_section", ""))
    relevance = float(citation.get("relevance_score", 0.0))
    return f"📄 {title} | Section: {section} | Relevance: {relevance:.2f}"


def _load_conversation_messages(client: httpx.Client, conversation_id: str) -> list[tuple[str, str]]:
    """Fetch messages for sidebar selection."""
    detail = _get_json(client, f"/chat/conversations/{conversation_id}")
    rows: list[tuple[str, str]] = []
    for message in detail.get("messages", []):
        role = str(message.get("role", "user"))
        content = str(message.get("content", ""))
        rows.append((role, content))
    return rows


def _sync_conversation_from_sidebar() -> None:
    """Load history when the user picks another conversation."""
    ids = st.session_state.get("conv_ids", [])
    idx = int(st.session_state.get("conv_picker", 0))
    if not ids or idx >= len(ids):
        return
    selected_id = ids[idx]
    st.session_state.conversation_id = selected_id
    try:
        with httpx.Client() as http_client:
            st.session_state.messages = _load_conversation_messages(http_client, selected_id)
        st.session_state.conv_load_error = None
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
        st.session_state.conv_load_error = str(exc)
        st.session_state.messages = []


def main() -> None:
    """Streamlit entrypoint."""
    st.set_page_config(page_title="RAG Knowledge Base", page_icon="💬", layout="wide")
    st.title("RAG Knowledge Base Assistant")
    st.caption(
        "Ask questions grounded in your indexed documents. Citations appear below each answer.",
    )

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = None
    if "conv_load_error" not in st.session_state:
        st.session_state.conv_load_error = None

    with st.sidebar:
        st.subheader("Conversations")
        if st.button("New conversation", type="primary"):
            st.session_state.conversation_id = None
            st.session_state.messages = []
            st.session_state.conv_load_error = None
            if "conv_picker" in st.session_state:
                del st.session_state["conv_picker"]
            st.rerun()

        try:
            with httpx.Client() as client:
                page_payload = _list_conversations(client)
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
            st.error("Could not load conversations: " + str(exc))
            page_payload = {"items": [], "total": 0}

        items = page_payload.get("items", [])
        labels: list[str] = []
        ids: list[str] = []
        for conversation in items:
            cid = str(conversation.get("id", ""))
            if not cid:
                continue
            updated = str(conversation.get("updated_at", ""))[:19]
            labels.append(f"{cid[:8]}… — {updated}")
            ids.append(cid)

        st.session_state.conv_ids = ids

        if labels:
            if "conv_picker" not in st.session_state:
                st.session_state.conv_picker = 0
            st.selectbox(
                "Open conversation",
                options=list(range(len(labels))),
                format_func=lambda i: labels[i],
                key="conv_picker",
                on_change=_sync_conversation_from_sidebar,
            )
            if st.session_state.conv_load_error:
                st.warning(st.session_state.conv_load_error)
        else:
            st.info("No conversations yet. Send a message to start.")

        st.divider()
        st.caption(f"API: `{_api_base()}`")

    for role, content in st.session_state.messages:
        with st.chat_message(role):
            st.markdown(content)

    if prompt := st.chat_input("Ask a question about your documents…"):
        st.session_state.messages.append(("user", prompt))
        with st.chat_message("assistant"), st.spinner("Retrieving and generating…"):
            try:
                with httpx.Client() as client:
                    data = _post_query(
                        client,
                        question=prompt,
                        conversation_id=st.session_state.conversation_id,
                    )
            except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
                st.error(f"Request failed: {exc}")
                st.session_state.messages.append(("assistant", f"Error: {exc}"))
            else:
                conv_id = data.get("conversation_id")
                if conv_id:
                    st.session_state.conversation_id = str(conv_id)

                if data.get("refused"):
                    reason = data.get("refusal_reason") or "unspecified"
                    st.warning(f"Request could not be completed. Reason: **{reason}**")
                    answer_text = str(data.get("answer", ""))
                    st.markdown(answer_text)
                    st.session_state.messages.append(
                        ("assistant", answer_text or "(refused)"),
                    )
                else:
                    answer_text = str(data.get("answer", ""))
                    st.markdown(answer_text)
                    citations = data.get("citations") or []
                    if citations:
                        st.markdown("**Sources**")
                        cite_lines: list[str] = []
                        for cite in citations:
                            line = _format_citation(cite)
                            st.markdown(f"- {line}")
                            cite_lines.append(line)
                        full_assistant = f"{answer_text}\n\n**Sources**\n" + "\n".join(cite_lines)
                    else:
                        full_assistant = answer_text
                    st.session_state.messages.append(("assistant", full_assistant))


if __name__ == "__main__":
    main()
