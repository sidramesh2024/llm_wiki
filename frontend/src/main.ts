type Source = "knowledge" | "context" | "web" | "mixed" | "error";

interface ChatResponse {
  session_id: string;
  answer: string;
  source: Source;
  agents: string[];
}

const PROMPTS = [
  "Can we write Texwin Acquisitions for the Houston warehouse?",
  "What IKE articles fire for the Houston warehouse?",
  "If we bind Baxter and Goya, do we breach the Puerto Rico cap?",
  "What is the weather in Houston right now?",
];

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("missing #app");

app.innerHTML = `
  <aside>
    <div class="eyebrow">Graph RAG</div>
    <h1>Underwriting desk</h1>
    <p>Fictional commercial-property book. The desk walks the ontology, searches three datastores, and uses the web only when the book has no answer.</p>
    <h2>Ontology</h2>
    <ul>
      <li>Account <span class="store">uw-accounts</span></li>
      <li>Guideline <span class="store">uw-guidelines</span></li>
      <li>Location and Accumulation <span class="store">uw-exposures</span></li>
    </ul>
    <p class="hint">HAS_LOCATION, GOVERNED_BY, COUNTS_TOWARD, CAPPED_BY. Figures are not real submissions.</p>
  </aside>
  <main>
    <div class="thread" id="thread"></div>
    <div class="prompts" id="prompts"></div>
    <form class="composer" id="form">
      <textarea id="input" rows="2" placeholder="Ask about an account, a location, or something outside the book"></textarea>
      <button type="submit" id="send">Send</button>
    </form>
  </main>
`;

const thread = document.querySelector<HTMLDivElement>("#thread")!;
const form = document.querySelector<HTMLFormElement>("#form")!;
const input = document.querySelector<HTMLTextAreaElement>("#input")!;
const send = document.querySelector<HTMLButtonElement>("#send")!;
const prompts = document.querySelector<HTMLDivElement>("#prompts")!;

let sessionId: string | null = null;
let userId = localStorage.getItem("uw-user");
if (!userId) {
  userId = crypto.randomUUID();
  localStorage.setItem("uw-user", userId);
}

function addBubble(role: "user" | "assistant", text: string, source?: Source): void {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  if (source) {
    const tag = document.createElement("div");
    tag.className = `tag ${source}`;
    tag.textContent = source === "error" ? "error" : source;
    bubble.appendChild(tag);
  }
  const body = document.createElement("div");
  body.textContent = text;
  bubble.appendChild(body);
  thread.appendChild(bubble);
  thread.scrollTop = thread.scrollHeight;
}

async function apiBase(): Promise<string> {
  const response = await fetch("/config.json", { cache: "no-store" });
  if (!response.ok) return "http://127.0.0.1:8080";
  const config = (await response.json()) as { apiBase?: string };
  return (config.apiBase || "http://127.0.0.1:8080").replace(/\/$/, "");
}

async function ask(message: string): Promise<void> {
  addBubble("user", message);
  send.disabled = true;
  try {
    const base = await apiBase();
    const response = await fetch(`${base}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId, user_id: userId }),
    });
    const payload = (await response.json()) as ChatResponse & { detail?: string };
    if (!response.ok) {
      const detail = typeof payload.detail === "string" ? payload.detail : response.statusText;
      addBubble("assistant", detail, "error");
      return;
    }
    sessionId = payload.session_id;
    addBubble("assistant", payload.answer, payload.source);
  } catch (err) {
    addBubble("assistant", err instanceof Error ? err.message : "request failed", "error");
  } finally {
    send.disabled = false;
    input.focus();
  }
}

for (const prompt of PROMPTS) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = prompt;
  button.addEventListener("click", () => {
    input.value = prompt;
    void ask(prompt);
  });
  prompts.appendChild(button);
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || send.disabled) return;
  input.value = "";
  void ask(message);
});
