"""Authenticated, read-only chat and explicit draft clarification endpoints."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from flask import Blueprint, Flask, current_app, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fingpt.adapters.foundry_chat import ChatInferenceError
from fingpt.services.chat import NoClarification, clarify_item, monthly_summary
from fingpt.services.chat_orchestration import ChatIntent, ChatOrchestrator, is_explicit_question
from fingpt.services.drafts import (
    ConversationNotFound,
    DraftConfirmationConflict,
    DraftIdempotencyConflict,
    create_text_draft,
)

CHAT_BLUEPRINT = Blueprint("chat", __name__, url_prefix="/api/chat")


def register_chat(app: Flask) -> None:
    """Register owner-scoped chat routes behind the API auth and CSRF middleware."""
    app.register_blueprint(CHAT_BLUEPRINT)


@CHAT_BLUEPRINT.post("/ask")
def ask() -> tuple[Any, int]:
    """Answer general questions using bounded, database-derived monthly totals only."""
    payload = request.get_json(silent=True)
    if (
        not isinstance(payload, dict)
        or set(payload) - {"question", "language", "history"}
        or not {"question", "language"} <= set(payload)
    ):
        return _error("invalid_request", "Send a question and a language."), 400
    question = payload["question"]
    language = payload["language"]
    if not isinstance(question, str) or not question.strip() or len(question) > 1_000:
        return _error("invalid_question", "Question must contain 1–1,000 characters."), 422
    if language not in ("vi", "en"):
        return _error("invalid_language", "Choose vi or en."), 422
    history = payload.get("history", [])
    if (
        not isinstance(history, list)
        or len(history) > 8
        or any(
            not isinstance(turn, dict)
            or set(turn) != {"role", "text"}
            or turn["role"] not in ("user", "assistant")
            or not isinstance(turn["text"], str)
            or len(turn["text"]) > 1_000
            for turn in history
        )
    ):
        return _error("invalid_history", "Chat history is invalid."), 422
    assistant = current_app.extensions.get("chat_assistant")
    if assistant is None:
        return _error("chat_unavailable", "AI chat is not configured."), 503
    database: Session = g.database_session
    try:
        context = monthly_summary(database, g.auth_user.id)
        answer = assistant.answer(question=question.strip(), language=language, context=context, history=history)
    except SQLAlchemyError:
        current_app.logger.warning("Chat summary query failed")
        return _error("service_unavailable", "Chat data is temporarily unavailable."), 503
    except ChatInferenceError:
        current_app.logger.warning("Chat inference unavailable")
        return _error("chat_unavailable", "AI chat is temporarily unavailable."), 503
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 8_000:
        return _error("invalid_answer", "AI chat returned an invalid answer."), 503
    return jsonify(answer=answer), 200


@CHAT_BLUEPRINT.post("/message")
def message() -> tuple[Any, int]:
    """Coordinate one chat surface across read-only Q&A and draft-intake agents."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) - {"message", "language", "history", "draftContext"}:
        return _error("invalid_request", "Send a message, language, and optional history."), 400
    text = payload.get("message")
    language = payload.get("language")
    history = payload.get("history", [])
    if not isinstance(text, str) or not text.strip() or len(text) > 8_000:
        return _error("invalid_message", "Message must contain 1–8,000 characters."), 422
    if language not in ("vi", "en"):
        return _error("invalid_language", "Choose vi or en."), 422
    if not _valid_history(history):
        return _error("invalid_history", "Chat history is invalid."), 422

    message_text = text.strip()
    orchestrator: ChatOrchestrator = current_app.extensions["chat_orchestrator"]
    intent = orchestrator.route(message_text, has_pending_clarification=payload.get("draftContext") is not None)
    if intent is ChatIntent.TRANSACTION:
        return _create_transaction_draft(message_text)
    if intent is ChatIntent.INSIGHT:
        if len(message_text) > 1_000:
            return _error("invalid_question", "Questions must be at most 1,000 characters."), 422
        return _answer_insight(language)

    draft_context, context_error = _validated_draft_context(payload.get("draftContext"))
    if context_error is not None:
        return context_error
    if draft_context is not None and not is_explicit_question(message_text):
        if len(message_text) > 1_000:
            return _error("invalid_reply", "Draft replies must be at most 1,000 characters."), 422
        clarification = _try_clarification(message_text, draft_context)
        if clarification is not None:
            return clarification
    if len(message_text) > 1_000:
        return _error("invalid_question", "Questions must be at most 1,000 characters."), 422
    return _answer_question(message_text, language, history)


def _validated_draft_context(
    value: Any,
) -> tuple[tuple[UUID, UUID, int] | None, tuple[Any, int] | None]:
    """Validate the optional active draft item supplied for a chat follow-up."""
    if value is None:
        return None, None
    if not isinstance(value, dict) or set(value) != {"draftId", "itemId", "expectedVersion"}:
        return None, (_error("invalid_draft_context", "Draft context fields are invalid."), 400)
    try:
        draft_id = UUID(value["draftId"])
        item_id = UUID(value["itemId"])
    except (ValueError, TypeError, AttributeError):
        return None, (_error("invalid_draft_context", "Draft and item IDs must be UUIDs."), 400)
    version = value["expectedVersion"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return None, (_error("invalid_draft_context", "Draft item version must be positive."), 400)
    return (draft_id, item_id, version), None


def _try_clarification(text: str, context: tuple[UUID, UUID, int]) -> tuple[Any, int] | None:
    """Let the draft specialist handle a supported follow-up; otherwise allow Q&A."""
    draft_id, item_id, version = context
    database: Session = g.database_session
    try:
        updated = clarify_item(
            database,
            user_id=g.auth_user.id,
            draft_id=draft_id,
            item_id=item_id,
            version=version,
            text=text,
        )
    except NoClarification:
        return None
    except LookupError:
        return _error("draft_item_not_found", "Draft item was not found."), 404
    except DraftConfirmationConflict:
        return _error("stale_draft", "Draft changed; reload it before replying."), 409
    except ValueError:
        return _error("invalid_reply", "Reply could not be applied to this draft."), 422
    except SQLAlchemyError:
        database.rollback()
        current_app.logger.warning("Draft clarification failed")
        return _error("service_unavailable", "Draft service is temporarily unavailable."), 503

    from fingpt.api.drafts import _serialize_item

    return jsonify(intent=ChatIntent.CLARIFICATION, item=_serialize_item(updated)), 200


def _create_transaction_draft(text: str) -> tuple[Any, int]:
    """Send transaction-intent text to deterministic parsing and durable preview."""
    if re.search(
        r"[$€£]|\b(usd|eur|gbp|cad|aud|jpy|cny|dollars?|bucks|euros?|pounds?|yen)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return _error("unsupported_currency", "Confirm the currency in the transaction preview."), 422
    try:
        idempotency_key = str(UUID(request.headers.get("Idempotency-Key", "")))
    except ValueError:
        return _error("invalid_idempotency_key", "Send a valid UUID in Idempotency-Key."), 400

    database: Session = g.database_session
    try:
        snapshot, replayed = create_text_draft(
            database,
            user_id=g.auth_user.id,
            text=text,
            idempotency_key=idempotency_key,
            conversation_id=None,
            intake_at=datetime.now(UTC),
        )
    except DraftIdempotencyConflict as error:
        return _error("idempotency_conflict", str(error)), 409
    except ConversationNotFound:
        return _error("conversation_not_found", "Conversation was not found."), 404
    except ValueError as error:
        return _error("invalid_draft", str(error)), 422
    except SQLAlchemyError:
        database.rollback()
        current_app.logger.warning("Chat transaction draft creation failed")
        return _error("service_unavailable", "Draft service is temporarily unavailable."), 503

    from fingpt.api.drafts import _serialize_draft

    return jsonify(intent=ChatIntent.TRANSACTION, draft=_serialize_draft(snapshot)), 200 if replayed else 201


def _answer_question(text: str, language: str, history: list[dict[str, str]]) -> tuple[Any, int]:
    """Send read-only chat to the Q&A specialist with aggregate-only context."""
    assistant = current_app.extensions.get("chat_assistant")
    if assistant is None:
        return _error("chat_unavailable", "AI chat is not configured."), 503
    database: Session = g.database_session
    try:
        context = monthly_summary(database, g.auth_user.id)
        answer = assistant.answer(question=text, language=language, context=context, history=history)
    except SQLAlchemyError:
        current_app.logger.warning("Chat summary query failed")
        return _error("service_unavailable", "Chat data is temporarily unavailable."), 503
    except ChatInferenceError:
        current_app.logger.warning("Chat inference unavailable")
        return _error("chat_unavailable", "AI chat is temporarily unavailable."), 503
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 8_000:
        return _error("invalid_answer", "AI chat returned an invalid answer."), 503
    return jsonify(intent=ChatIntent.QUESTION, answer=answer), 200


@CHAT_BLUEPRINT.get("/insight")
def insight() -> tuple[Any, int]:
    """Return one read-only spending-trend sentence for the Dashboard widget."""
    language = request.args.get("language", "vi")
    if language not in ("vi", "en"):
        return _error("invalid_language", "Choose vi or en."), 422
    return _answer_insight(language)


def _answer_insight(language: str) -> tuple[Any, int]:
    """Send a trend/comparison question to the read-only insight specialist."""
    specialist = current_app.extensions.get("insight_specialist")
    if specialist is None:
        return _error("chat_unavailable", "AI chat is not configured."), 503
    database: Session = g.database_session
    try:
        answer = specialist.narrate(database, g.auth_user.id, language=language)
    except SQLAlchemyError:
        current_app.logger.warning("Insight query failed")
        return _error("service_unavailable", "Chat data is temporarily unavailable."), 503
    return jsonify(intent=ChatIntent.INSIGHT, answer=answer), 200


def _valid_history(history: Any) -> bool:
    """Reject unbounded or malformed client conversation context."""
    return (
        isinstance(history, list)
        and len(history) <= 8
        and all(
            isinstance(turn, dict)
            and set(turn) == {"role", "text"}
            and turn["role"] in ("user", "assistant")
            and isinstance(turn["text"], str)
            and len(turn["text"]) <= 1_000
            for turn in history
        )
    )


@CHAT_BLUEPRINT.post("/drafts/<uuid:draft_id>/reply")
def reply_to_draft(draft_id: UUID) -> tuple[Any, int]:
    """Apply only explicit missing date/period facts to one versioned draft item."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"itemId", "expectedVersion", "text"}:
        return _error("invalid_request", "Send itemId, expectedVersion, and text."), 400
    text = payload["text"]
    version = payload["expectedVersion"]
    if not isinstance(text, str) or not text.strip() or len(text) > 1_000:
        return _error("invalid_text", "Reply must contain 1–1,000 characters."), 422
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return _error("invalid_version", "expectedVersion must be positive."), 400
    try:
        item_id = UUID(payload["itemId"])
    except (ValueError, TypeError, AttributeError):
        return _error("invalid_item", "itemId must be a UUID."), 400
    database: Session = g.database_session
    try:
        updated = clarify_item(
            database, user_id=g.auth_user.id, draft_id=draft_id, item_id=item_id, version=version, text=text
        )
    except LookupError:
        return _error("draft_item_not_found", "Draft item was not found."), 404
    except DraftConfirmationConflict:
        return _error("stale_draft", "Draft changed; reload it before replying."), 409
    except NoClarification:
        return _error("no_clarification", "Reply with one missing date or day period."), 422
    except ValueError:
        return _error("invalid_reply", "Reply could not be applied to this draft."), 422
    except SQLAlchemyError:
        database.rollback()
        current_app.logger.warning("Draft clarification failed")
        return _error("service_unavailable", "Draft is temporarily unavailable."), 503
    from fingpt.api.drafts import _serialize_item

    return jsonify(item=_serialize_item(updated)), 200


def _error(code: str, message: str) -> Any:
    """Return a bounded, user-safe error without logging financial content."""
    return jsonify(error={"code": code, "message": message})
