"""FastAPI bridge from the chat UI to the Agent Engine underwriting desk."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import google.auth
import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.auth.transport.requests import Request
from google.genai import types
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
    return {"ok": True, "engine_configured": bool(ENGINE), "avatar": "Ben"}


def _live_config() -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["VIDEO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
            )
        ),
        avatar_config=types.AvatarConfig(avatar_name="Ben"),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=types.Content(
            parts=[
                types.Part.from_text(
                    text=(
                        "You are a concise spoken assistant. Answer in a sentence or two. "
                        "The person can see your face, so do not describe yourself."
                    )
                )
            ]
        ),
    )


@app.websocket("/api/live")
async def live(websocket: WebSocket) -> None:
    """Proxy the browser to Gemini 3.8 Live with the Ben avatar."""
    await websocket.accept()
    client = genai.Client(enterprise=True, project=PROJECT, location=LOCATION)
    try:
        async with client.aio.live.connect(model="gemini-3.8-live", config=_live_config()) as session:
            await websocket.send_json({"type": "ready", "avatar": "Ben", "voice": "Puck"})

            async def browser_to_model() -> None:
                while True:
                    incoming = await websocket.receive()
                    if incoming.get("type") == "websocket.disconnect":
                        return
                    audio = incoming.get("bytes")
                    if audio:
                        await session.send_realtime_input(
                            audio=types.Blob(data=audio, mime_type="audio/pcm;rate=16000")
                        )
                        continue
                    raw = incoming.get("text")
                    if not raw:
                        continue
                    payload = json.loads(raw)
                    kind = payload.get("type")
                    if kind == "text" and payload.get("text"):
                        await session.send_realtime_input(text=str(payload["text"]))
                    elif kind == "audio_end":
                        await session.send_realtime_input(audio_stream_end=True)

            async def model_to_browser() -> None:
                # receive() stops at the end of each turn. Keep reading so the
                # next question stays on the same live session.
                while True:
                    async for message in session.receive():
                        content = message.server_content
                        if content is None:
                            continue
                        if content.input_transcription and content.input_transcription.text:
                            await websocket.send_json(
                                {"type": "transcript", "role": "user", "text": content.input_transcription.text}
                            )
                        if content.output_transcription and content.output_transcription.text:
                            await websocket.send_json(
                                {
                                    "type": "transcript",
                                    "role": "model",
                                    "text": content.output_transcription.text,
                                }
                            )
                        turn = content.model_turn
                        if turn and turn.parts:
                            for part in turn.parts:
                                blob = part.inline_data
                                if blob and blob.data and (blob.mime_type or "").startswith("video/"):
                                    await websocket.send_bytes(b"\x01" + blob.data)
                        if content.turn_complete:
                            await websocket.send_json({"type": "turn_complete"})

            incoming = asyncio.create_task(browser_to_model())
            outgoing = asyncio.create_task(model_to_browser())
            done, pending = await asyncio.wait(
                {incoming, outgoing}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                task.result()
    except WebSocketDisconnect:
        return
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            return


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
