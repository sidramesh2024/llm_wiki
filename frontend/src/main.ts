const MIME = 'video/mp4; codecs="avc1.42C01F, mp4a.40.2"';

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("missing #app");

app.innerHTML = `
  <section class="stage">
    <video id="avatar" playsinline autoplay></video>
    <p id="status">Connecting to Ben…</p>
  </section>
  <section class="desk">
    <header>
      <p class="eyebrow">Gemini 3.8 Live</p>
      <h1>Avatar chat</h1>
      <p class="hint">Preset avatar Ben, voice Puck. Talk, or type a line. A custom face is not required.</p>
    </header>
    <div class="thread" id="thread"></div>
    <div class="composer">
      <button type="button" id="mic">Talk</button>
      <form id="form">
        <input id="input" placeholder="Or type a message" autocomplete="off" />
        <button type="submit" id="send">Send</button>
      </form>
    </div>
  </section>
`;

const video = document.querySelector<HTMLVideoElement>("#avatar")!;
const statusLine = document.querySelector<HTMLParagraphElement>("#status")!;
const thread = document.querySelector<HTMLDivElement>("#thread")!;
const mic = document.querySelector<HTMLButtonElement>("#mic")!;
const form = document.querySelector<HTMLFormElement>("#form")!;
const input = document.querySelector<HTMLInputElement>("#input")!;

class AvatarPlayer {
  private media: MediaSource | null = null;
  private buffer: SourceBuffer | null = null;
  private queue: ArrayBuffer[] = [];
  private opened = false;
  private failed = false;

  push(bytes: Uint8Array): void {
    const copy = bytes.slice().buffer;
    if (this.failed) return;
    if (!this.media || (this.opened && isInit(bytes))) {
      this.reset();
    }
    this.queue.push(copy);
    this.pump();
  }

  private reset(): void {
    if (this.media && this.media.readyState === "open") {
      try {
        this.media.endOfStream();
      } catch {
        /* a new stream replaces this one */
      }
    }
    this.buffer = null;
    this.opened = false;
    this.media = new MediaSource();
    video.src = URL.createObjectURL(this.media);
    this.media.addEventListener("sourceopen", () => {
      if (!this.media || this.buffer) return;
      try {
        this.buffer = this.media.addSourceBuffer(MIME);
        this.buffer.mode = "sequence";
        this.buffer.addEventListener("updateend", () => this.pump());
        this.opened = true;
        this.pump();
      } catch (err) {
        this.failed = true;
        statusLine.textContent = err instanceof Error ? err.message : "Could not play the avatar video";
      }
    });
  }

  private pump(): void {
    const buffer = this.buffer;
    if (!buffer || buffer.updating || this.queue.length === 0) return;
    const chunk = this.queue.shift();
    if (!chunk) return;
    try {
      buffer.appendBuffer(chunk);
      void video.play();
    } catch (err) {
      this.failed = true;
      statusLine.textContent = err instanceof Error ? err.message : "Could not append avatar video";
    }
  }
}

function isInit(bytes: Uint8Array): boolean {
  return bytes.length > 8 && bytes[4] === 0x66 && bytes[5] === 0x74 && bytes[6] === 0x79 && bytes[7] === 0x70;
}

const player = new AvatarPlayer();
let socket: WebSocket | null = null;

let ready = false;
let listening = false;
let audioContext: AudioContext | null = null;
let micStream: MediaStream | null = null;
let worklet: AudioWorkletNode | null = null;
const openLine: { role: "user" | "model" | null; el: HTMLDivElement | null } = { role: null, el: null };

function connect(): void {
  ready = false;
  const next = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/live`);
  next.binaryType = "arraybuffer";
  socket = next;
  setStatus("Connecting to Ben…");

  next.addEventListener("message", (event) => {
    if (event.data instanceof ArrayBuffer) {
      const bytes = new Uint8Array(event.data);
      if (bytes[0] === 1) player.push(bytes.subarray(1));
      return;
    }
    const payload = JSON.parse(String(event.data)) as {
      type: string;
      role?: "user" | "model";
      text?: string;
      message?: string;
    };
    if (payload.type === "ready") {
      ready = true;
      setStatus(listening ? "Listening" : "Ben is listening. Press Talk, or type a message.");
    } else if (payload.type === "transcript" && payload.role && payload.text) {
      appendTranscript(payload.role, payload.text);
      setStatus(payload.role === "model" ? "Ben is speaking" : "Hearing you");
    } else if (payload.type === "turn_complete") {
      openLine.role = null;
      openLine.el = null;
      setStatus(listening ? "Listening" : "Ben is listening. Press Talk, or type a message.");
    } else if (payload.type === "error") {
      setStatus(payload.message || "Live session failed");
    }
  });

  next.addEventListener("close", () => {
    if (socket !== next) return;
    ready = false;
    setStatus("Reconnecting…");
    window.setTimeout(connect, 800);
  });
  next.addEventListener("error", () => {
    if (socket === next) setStatus("Reconnecting…");
  });
}

function setStatus(text: string): void {
  statusLine.textContent = text;
}

function addLine(role: "user" | "model", text: string): HTMLDivElement {
  const line = document.createElement("div");
  line.className = `line ${role}`;
  const who = document.createElement("span");
  who.textContent = role === "user" ? "You" : "Ben";
  const body = document.createElement("p");
  body.textContent = text;
  line.append(who, body);
  thread.appendChild(line);
  thread.scrollTop = thread.scrollHeight;
  return line;
}

function appendTranscript(role: "user" | "model", text: string): void {
  if (openLine.role !== role || !openLine.el) {
    openLine.role = role;
    openLine.el = addLine(role, text);
    return;
  }
  const body = openLine.el.querySelector("p");
  if (body) body.textContent = `${body.textContent ?? ""}${text}`;
  thread.scrollTop = thread.scrollHeight;
}

async function startMic(): Promise<void> {
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  audioContext = new AudioContext();
  await audioContext.audioWorklet.addModule("/pcm-worklet.js");
  const source = audioContext.createMediaStreamSource(micStream);
  worklet = new AudioWorkletNode(audioContext, "pcm-downsampler");
  worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
    if (socket?.readyState === WebSocket.OPEN) socket.send(event.data);
  };
  source.connect(worklet);
  listening = true;
  mic.textContent = "Stop";
  setStatus("Listening");
}

function stopMic(): void {
  listening = false;
  worklet?.disconnect();
  worklet = null;
  micStream?.getTracks().forEach((track) => track.stop());
  micStream = null;
  void audioContext?.close();
  audioContext = null;
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "audio_end" }));
  }
  mic.textContent = "Talk";
  setStatus("Ben is listening. Press Talk, or type a message.");
}

mic.addEventListener("click", () => {
  void video.play();
  if (!ready) return;
  if (listening) stopMic();
  else void startMic().catch((err: unknown) => setStatus(err instanceof Error ? err.message : "Microphone blocked"));
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || !ready || socket?.readyState !== WebSocket.OPEN) return;
  void video.play();
  input.value = "";
  openLine.role = null;
  openLine.el = null;
  addLine("user", text);
  openLine.role = null;
  openLine.el = null;
  socket?.send(JSON.stringify({ type: "text", text }));
  setStatus("Ben is answering");
});

connect();
