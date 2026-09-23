"""FastAPI bridge from the chat UI to the Agent Engine underwriting desk."""

from __future__ import annotations

import json
import os
from typing import Any

import google.auth
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google.auth.transport.requests import Request
from pydantic import BaseModel, Field

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "gcpexplore-487204")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
ENGINE = os.environ.get("REASONING_ENGINE", "")

app = FastAPI(title="Underwriting desk")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    user_id: str = Field(default="desk", min_length=1, max_length=80)


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    source: str
    agents: list[str]


def _token() -> str:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if not creds.valid:
        creds.refresh(Request())
    return creds.token


def _engine() -> str:
    if not ENGINE:
        raise HTTPException(status_code=503, detail="REASONING_ENGINE is not configured")
    return ENGINE


def _url(method: str) -> str:
    return (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1/{_engine()}:{method}"
    )


async def _post(method: str, payload: dict, timeout: float) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            _url(method),
            headers={
                "Authorization": f"Bearer {_token()}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text[:2000])
    return response


def _session_id(body: Any) -> str:
    if isinstance(body, dict):
        output = body.get("output", body)
        if isinstance(output, dict):
            for key in ("id", "session_id", "sessionId"):
                if output.get(key):
                    return str(output[key])
        if body.get("name"):
            return str(body["name"]).rstrip("/").split("/")[-1]
    raise HTTPException(status_code=502, detail=f"no session id in {body!r}"[:500])


def _events(text: str) -> list[dict]:
    text = text.strip()
    if not text:
        return []
    decoder = json.JSONDecoder()
    events: list[dict] = []
    idx = 0
    size = len(text)
    while idx < size:
        while idx < size and text[idx].isspace():
            idx += 1
        if idx >= size:
            break
        if text.startswith("data:", idx):
            idx += 5
            continue
        if text.startswith("[DONE]", idx):
            break
        try:
            item, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        if isinstance(item, list):
            events.extend(entry for entry in item if isinstance(entry, dict))
        elif isinstance(item, dict):
            events.append(item)
        idx = end
    return events


def _answer(events: list[dict]) -> tuple[str, list[str]]:
    authors: list[str] = []
    root_text = ""
    partial = ""
    fallback = ""
    for event in events:
        author = str(event.get("author") or "")
        if author and author not in authors:
            authors.append(author)
        parts = (event.get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        if not text:
            continue
        fallback = text
        if author in ("", "underwriting_root"):
            if event.get("partial"):
                partial += text
            else:
                root_text = text
                partial = ""
    answer = (root_text or partial or fallback).strip()
    return answer, authors


def _source(answer: str, authors: list[str]) -> tuple[str, str]:
    lines = answer.splitlines()
    first = lines[0].strip().lower() if lines else ""
    body = "\n".join(lines[1:]).strip() if first.startswith("source:") else answer
    if "knowledge+web" in first or ("knowledge" in first and "web" in first):
        return "mixed", body
    if first.startswith("source:") and "context" in first:
        return "context", body
    if first.startswith("source:") and "web" in first:
        return "web", body
    if first.startswith("source:"):
        return "knowledge", body
    if "web_search_agent" in authors and "knowledge_search_agent" in authors:
        return "mixed", answer
    if "web_search_agent" in authors:
        return "web", answer
    return "knowledge", answer


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "engine_configured": bool(ENGINE)}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    session_id = body.session_id
    if not session_id:
        created = await _post(
            "query",
            {"class_method": "async_create_session", "input": {"user_id": body.user_id}},
            timeout=60,
        )
        session_id = _session_id(created.json())
    streamed = await _post(
        "streamQuery",
        {
            "class_method": "async_stream_query",
            "input": {
                "user_id": body.user_id,
                "session_id": session_id,
                "message": body.message,
            },
        },
        timeout=180,
    )
    answer, authors = _answer(_events(streamed.text))
    if not answer:
        raise HTTPException(status_code=502, detail="agent returned no text")
    source, answer = _source(answer, authors)
    return ChatResponse(
        session_id=session_id,
        answer=answer,
        source=source,
        agents=authors,
    )
