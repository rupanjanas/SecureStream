const AUTH_URL = import.meta.env.VITE_BACKEND_URL;  // Express server (auth/session)
const AI_URL   = import.meta.env.VITE_AI_SERVICE_URL; // Python AI service (docs/ingest)

// ── Session ────────────────────────────────────────────────────────────────────
export async function getSession() {
  try {
    const res = await fetch(`${AUTH_URL}/`, { credentials: "include" });
    if (!res.ok) return null;
    const data = await res.json();
    if (!data.isAuthenticated || !data.access_token) return null;
    return data;
  } catch {
    return null;
  }
}

// ── Health ─────────────────────────────────────────────────────────────────────
export async function getHealth() {
  try {
    const res = await fetch(`${AI_URL}/health`);
    if (!res.ok) return { status: "error", db: "error" };
    return res.json();
  } catch {
    return { status: "error", db: "error" };
  }
}

// ── Upload / Ingest ────────────────────────────────────────────────────────────
// Hits AI_URL/ingest — do NOT set Content-Type manually with FormData,
// the browser sets it automatically with the correct multipart boundary.
export async function uploadDocument(file, token, orgId = null) {
  if (!token) throw new Error("No access token — cannot upload");

  const formData = new FormData();
  formData.append("file", file);

  const headers = { Authorization: `Bearer ${token}` };
  if (orgId) headers["X-Org-Id"] = orgId;

  const res = await fetch(`${AI_URL}/ingest`, {
    method:  "POST",
    headers,           // ← no Content-Type here — browser sets multipart boundary
    body:    formData,
  });

  if (!res.ok) {
    const msg = await res.text().catch(() => `HTTP ${res.status}`);
    throw new Error(msg);
  }
  return res.json();
}

// ── List Documents ─────────────────────────────────────────────────────────────
// Hits AI_URL/documents — NOT AUTH_URL
export async function listDocuments(token, orgId = null) {
  if (!token) return { documents: [] };

  const headers = { Authorization: `Bearer ${token}` };
  if (orgId) headers["X-Org-Id"] = orgId;

  const res = await fetch(`${AI_URL}/documents`, {
    headers,
    credentials: "include",
  });

  if (!res.ok) {
    console.error(`listDocuments failed: ${res.status}`);
    return { documents: [] };
  }
  return res.json();
}

// ── Get Document Text ──────────────────────────────────────────────────────────
export async function getDocumentText(docName, token, orgId = null) {
  if (!token || !docName) return { text: "" };

  const headers = { Authorization: `Bearer ${token}` };
  if (orgId) headers["X-Org-Id"] = orgId;

  const res = await fetch(
    `${AI_URL}/documents/${encodeURIComponent(docName)}/text`,
    { headers, credentials: "include" }
  );

  if (!res.ok) return { text: "" };
  return res.json();
}

// ── Streaming Q&A ──────────────────────────────────────────────────────────────
export async function askQuestionStream(
  question,
  docName,
  chatHistory,
  onToken,
  onDone,
  topK  = 5,
  orgId = null,
) {
  // Always get a fresh token — don't rely on a stale tokenRef from component
  const session = await getSession();
  const token   = session?.access_token;

  if (!token) {
    throw new Error("Session expired — please log in again.");
  }

  const headers = {
    "Content-Type": "application/json",
    Authorization:  `Bearer ${token}`,
  };
  if (orgId) headers["X-Org-Id"] = orgId;

  const res = await fetch(`${AI_URL}/query/stream`, {
    method:  "POST",
    headers,
    credentials: "include",
    body: JSON.stringify({
      question,
      top_k:        topK,
      doc_name:     docName  || null,
      chat_history: Array.isArray(chatHistory) ? chatHistory.slice(-6) : [],
    }),
  });

  if (!res.ok) {
    const msg = await res.text().catch(() => `HTTP ${res.status}`);
    throw new Error(msg);
  }

  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let   buffer  = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop(); // keep incomplete last line for next chunk

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(line.slice(6));
        if (data.token && !data.done) onToken(data.token);
        if (data.done) onDone(data.sources || [], data.source_passages || []);
      } catch {
        continue; // skip malformed SSE lines
      }
    }
  }
}