# Avatar speech-to-speech

The chat UI talks to **Gemini 3.8 Live** (`gemini-3.8-live`). The model hears the user, answers out loud, and streams a talking face in the same video. The default face is the preset avatar **Ben**. The default voice is **Puck**.

The spoken session does not query a Vertex AI Search datastore. The underwriting stores (`uw-accounts`, `uw-guidelines`, `uw-exposures`) and the `uw-search` engine were removed from project `gcpexplore-487204`. Ben answers from the live model itself. The older book-retrieval path is described in `context_graph.md` and is not connected to this WebSocket.

## What runs where

```
Browser  frontend/  (Vite, http://127.0.0.1:5173)
  |  microphone  ->  16 kHz PCM over WebSocket /api/live
  |  typed text  ->  JSON { type: "text" }
  |  video/mp4   <-  binary frames, played in <video>
  v
FastAPI  backend/main.py  (http://127.0.0.1:8080)
  |  Google Gen AI SDK live.connect
  v
Gemini Enterprise Agent Platform
  model gemini-3.8-live
  project gcpexplore-487204
  location us-central1
```

Vite proxies `/api`, including the WebSocket, to the API (`frontend/vite.config.ts`).

| Piece | File |
|---|---|
| Page, mic, transcript, video player | `frontend/src/main.ts` |
| Layout | `frontend/src/style.css` |
| Downsample mic audio to 16 kHz PCM | `frontend/public/pcm-worklet.js` |
| Live session proxy | `backend/main.py` function `live` |
| SDK | `google-genai` in `backend/requirements.txt` |

Start the API, then the page:

```bash
.venv/bin/uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8080
cd frontend && npm run dev
```

The browser opens http://127.0.0.1:5173. The API uses Application Default Credentials for project `gcpexplore-487204`.

## Default avatar

Set in `_live_config()`:

| Setting | Value |
|---|---|
| Model | `gemini-3.8-live` |
| Avatar | `Ben` (`AvatarConfig.avatar_name`) |
| Voice | `Puck` (`PrebuiltVoiceConfig.voice_name`) |
| Response modality | `VIDEO` |

`VIDEO` is what turns on Live Avatar. The model returns one `video/mp4` stream. Speech is inside that stream (AAC), lip-synced to the face at 24 FPS. The page does not play a separate audio track.

A custom face is a still portrait passed as `avatar_config.customized_avatar`, not a `.glb` model. Project `gcpexplore-487204` is not allowlisted for that feature, so the session uses Ben. A prepared portrait, if present, is `backend/avatar.png` and is gitignored.

## Speech-to-speech path

```mermaid
sequenceDiagram
  participant User
  participant Page as frontend/src/main.ts
  participant API as backend /api/live
  participant Live as gemini-3.8-live

  Page->>API: WebSocket /api/live
  API->>Live: live.connect avatar Ben, voice Puck, modality VIDEO
  API-->>Page: {"type":"ready"}
  User->>Page: Talk, or a typed line
  Page->>API: 16 kHz PCM bytes, or {"type":"text"}
  API->>Live: send_realtime_input
  Live-->>API: video/mp4 chunks, input and output transcripts
  API-->>Page: 0x01 plus MP4 bytes, and transcript JSON
  Page->>User: video of Ben speaking, transcript lines
```

1. The page opens `ws://<host>/api/live` as soon as it loads (`connect()` in `frontend/src/main.ts`).
2. The API accepts the socket and opens one Live session with `_live_config()`. When setup finishes it sends `{ "type": "ready", "avatar": "Ben", "voice": "Puck" }`.
3. **Talk** calls `getUserMedia` with echo cancellation. `pcm-worklet.js` downsamples the mic to 16 kHz and posts 100 ms chunks of 16-bit little-endian mono PCM. The page sends those bytes on the WebSocket. **Stop** sends `{ "type": "audio_end" }`, which the API forwards as `audio_stream_end`.
4. A typed line is `{ "type": "text", "text": "..." }`. The API calls `send_realtime_input(text=...)`.
5. Gemini Live runs voice activity detection on the PCM. It transcribes the user, generates the reply, and synthesizes Ben’s face and Puck’s voice together.
6. The API reads `session.receive()` in a loop. The SDK iterator stops at `turn_complete`, so the loop calls `receive()` again and the same Live session stays up for the next question.
7. For each server message the API forwards:
   - `input_transcription` as `{ "type": "transcript", "role": "user", "text" }`
   - `output_transcription` as `{ "type": "transcript", "role": "model", "text" }`
   - each `video/mp4` inline blob as a binary frame whose first byte is `0x01` and whose remaining bytes are the MP4 chunk
   - `{ "type": "turn_complete" }` when the spoken turn ends
8. `AvatarPlayer` appends those chunks to a Media Source buffer (`video/mp4; codecs="avc1.42C01F, mp4a.40.2"`, mode `sequence`). The first chunk is an init segment (`ftyp` / `dash`). Later chunks are media segments. The `<video>` element plays picture and speech together.

If the socket closes, the page waits 800 ms and calls `connect()` again. That opens a new Live session. It does not resume the previous one.

## Where the answer comes from

Ben’s reply is the model’s own generation for that turn. `_live_config()` does not attach `search_knowledge`, `expand_graph`, Vertex AI Search, or the session context graph. The system instruction only asks for a short spoken answer:

> You are a concise spoken assistant. Answer in a sentence or two. The person can see your face, so do not describe yourself.

`POST /api/chat` is still in `backend/main.py`. That route streams a query to a reasoning engine when `REASONING_ENGINE` is set. The avatar page never calls it. With the engine removed, that route cannot retrieve book passages either.

To have Ben answer from the underwriting book, the Live session would need a tool that searches a datastore and returns passages into the same turn. That tool is not implemented on `/api/live`.

## Audio and video formats

| Direction | Format |
|---|---|
| Mic into the model | 16 kHz, 16-bit, mono PCM (`audio/pcm;rate=16000`) |
| Model back to the page | Fragmented MP4 (`video/mp4`), H.264 `avc1.42C01F` plus AAC `mp4a.40.2` |
| Transcripts | Incremental text fragments, concatenated into the You and Ben lines |

The page copies each MP4 chunk before `appendBuffer`, because the buffer the socket delivered is detached by the player.
