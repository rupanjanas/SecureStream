const AUTH_URL = import.meta.env.VITE_BACKEND_URL;
const AI_URL   = import.meta.env.VITE_AI_SERVICE_URL;

// ── Session ────────────────────────────────────────────────────────────────────
export async function getSession() {
  try {
    const res = await fetch(`${AUTH_URL}/`, { credentials: "include" });
    if (!res.ok) return null;
    const data = await res.json();
    if (!data.isAuthenticated) return null;

    if (data.access_token && isTokenExpired(data.access_token)) {
      return await refreshSession();
    }

    return data.access_token ? data : null;
  } catch {
    return null;
  }
}

function isTokenExpired(token) {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return payload.exp * 1000 < Date.now() + 30_000;
  } catch {
    return false;
  }
}

async function refreshSession() {
  try {
    const res = await fetch(`${AUTH_URL}/refresh`, {
      method:      "POST",
      credentials: "include",
    });
    if (!res.ok) {
      window.location.href = `${AUTH_URL}/login`;
      return null;
    }
    const { access_token } = await res.json();
    return { access_token, isAuthenticated: true };
  } catch {
    return null;
  }
}

async function getFreshToken() {
  const session = await getSession();
  if (!session?.access_token) {
    throw new Error("Session expired — please log in again.");
  }
  return session.access_token;
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
export async function uploadDocument(file, token, orgId = null) {
  if (!token) throw new Error("No access token — cannot upload");

  const formData = new FormData();
  formData.append("file", file);

  const headers = { Authorization: `Bearer ${token}` };
  if (orgId && typeof orgId === "string" && orgId.trim()) {
    headers["X-Org-Id"] = orgId.trim();
  }

  const res = await fetch(`${AI_URL}/ingest`, {
    method:  "POST",
    headers,
    body:    formData,
  });

  if (!res.ok) {
    const msg = await res.text().catch(() => `HTTP ${res.status}`);
    throw new Error(msg);
  }
  return res.json();
}

// ── List Documents ─────────────────────────────────────────────────────────────
export async function listDocuments(token, orgId = null) {
  if (!token || isTokenExpired(token)) {
    const session = await getSession();
    token = session?.access_token;
  }
  if (!token) return { documents: [] };

  const headers = { Authorization: `Bearer ${token}` };
  // FIX: Same guard as uploadDocument — only set header for real org IDs.
  if (orgId && typeof orgId === "string" && orgId.trim()) {
    headers["X-Org-Id"] = orgId.trim();
  }

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
  if (orgId && typeof orgId === "string" && orgId.trim()) {
    headers["X-Org-Id"] = orgId.trim();
  }

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
  const token = await getFreshToken();
  if (!token) throw new Error("Session expired — please log in again.");

  const headers = {
    "Content-Type": "application/json",
    Authorization:  `Bearer ${token}`,
  };
  if (orgId && typeof orgId === "string" && orgId.trim()) {
    headers["X-Org-Id"] = orgId.trim();
  }

  const res = await fetch(`${AI_URL}/query/stream`, {
    method:  "POST",
    headers,
    credentials: "include",
    body: JSON.stringify({
      question,
      top_k:        topK,
      doc_name:     docName || null,
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
    buffer = lines.pop();

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(line.slice(6));
        if (data.token && !data.done) onToken(data.token);
        if (data.done) onDone(data.sources || [], data.source_passages || []);
      } catch {
        continue;
      }
    }
  }
}

// ── Shared Chat History ────────────────────────────────────────────────────────
// Stored in Supabase per (org_id, doc_name) so all org members see the same

export async function getSharedChatHistory(docName, token, orgId) {
  if (!token || !docName || !orgId) return { messages: [], sources: [] };

  try {
    const res = await fetch(
      `${AI_URL}/chat-history/${encodeURIComponent(docName)}`,
      {
        credentials: "include",
        headers: {
          Authorization: `Bearer ${token}`,
          "X-Org-Id":    orgId,
        },
      }
    );
    if (!res.ok) return { messages: [], sources: [] };
    return res.json();
  } catch {
    return { messages: [], sources: [] };
  }
}

export async function saveSharedChatHistory(docName, messages, sources, token, orgId) {
  if (!token || !docName || !orgId) return;

  // Fire-and-forget — never blocks the UI
  fetch(`${AI_URL}/chat-history`, {
    method:      "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Authorization:  `Bearer ${token}`,
      "X-Org-Id":     orgId,
    },
    body: JSON.stringify({ doc_name: docName, messages, sources }),
  }).catch((err) => console.warn("[chat-history] save failed:", err));
}