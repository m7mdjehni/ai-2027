from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app import crud, schemas
from app.database import get_db
from sqlalchemy.orm import Session
from app.ai import active_ai_provider, call_assistant, classify_ollama_error

router = APIRouter()


def ollama_http_error(error: Exception):
    """Map Ollama errors to HTTP status codes and user-friendly messages."""
    category = classify_ollama_error(error)
    error_msg = str(error)
    
    if category == "connection_error":
        return 503, f"Cannot connect to Ollama. {error_msg}"
    if category == "timeout_error":
        return 504, "Ollama request timed out. Please try again."
    if category == "model_not_found":
        return 404, "The configured Ollama model is not available. Verify with 'ollama list'."
    if category == "invalid_request":
        return 400, "The assistant request was rejected by Ollama."
    if category == "invalid_response":
        return 502, "Ollama returned an invalid response format."
    if category == "ollama_internal_error":
        return 500, "Ollama encountered an internal error."
    if category == "ollama_service_error":
        return 503, "Ollama service is unavailable. Please try again shortly."
    if category == "request_error" or category == "http_error":
        return 502, f"Ollama communication error: {error_msg}"
    
    return 502, f"Assistant service error: {error_msg}"

@router.post('/converse')
def converse(payload: dict, db: Session = Depends(get_db)):
    # payload: {"message": "..."}
    message = payload.get('message')
    if not isinstance(message, str) or not message.strip() or len(message) > 100_000:
        raise HTTPException(status_code=400, detail="message must be nonempty text (maximum 100000 characters)")
    # get recent conversation history
    history = crud.list_conversations(db, limit=20)
    history_serialized = [{"role": h.role, "message": h.message} for h in history]
    # save user message
    crud.create_conversation(db, role='user', message=message)
    # Ollama is the default; online routing requires explicit configuration.
    try:
        result = call_assistant(history_serialized, message)
        reply = result["response"]
    except Exception as error:
        if active_ai_provider() == "ollama":
            status_code, detail = ollama_http_error(error)
        else:
            detail = str(error)
            status_code = 504 if "timed out" in detail.lower() else 503
        raise HTTPException(status_code=status_code, detail=detail) from error
    if not reply or not isinstance(reply, str) or not reply.strip():
        raise HTTPException(status_code=502, detail="Assistant returned an empty response")
    crud.create_conversation(db, role='assistant', message=reply)
    return {
        "reply": reply,
        "task_type": result.get("task_type"),
        "model": result.get("model"),
        "provider": result.get("provider"),
        "fallback_used": result.get("fallback_used", False),
    }

@router.get('/conversations', response_model=List[schemas.ConversationRead])
def get_conversations(db: Session = Depends(get_db)):
    return crud.list_conversations(db, limit=200)
