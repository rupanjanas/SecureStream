const AUTH_URL = import.meta.env.VITE_BACKEND_URL;
const AI_URL   = import.meta.env.VITE_AI_SERVICE_URL;

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

export async function getHealth() {
  try {
    const res = await fetch(`${AI_URL}/health`);
    return res.json();
  } catch {
    return { status: "error", db: "error" };
  }
}

export async function uploadDocument(file, token, orgId = null) {
  const formData = new FormData();
  formData.append("file", file);

  const headers = { Authorization: `Bearer ${token}` };
  if (orgId) headers["X-Org-Id"] = orgId;

  const res = await fetch(`${AI_URL}/ingest`, {
    method: "POST",
    headers,
    body: formData,
  });

  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listDocuments(token, orgId = null) {
  const headers = { Authorization: `Bearer ${token}` };
  if (orgId) headers["X-Org-Id"] = orgId;

  const res = await fetch(`${AI_URL}/documents`, { headers });
  if (!res.ok) return { documents: [] };
  return res.json();
}

export async function getDocumentText(docName, token, orgId = null) {
  const headers = { Authorization: `Bearer ${token}` };
  if (orgId) headers["X-Org-Id"] = orgId;

  const res = await fetch(
    `${AI_URL}/documents/${encodeURIComponent(docName)}/text`,
    { headers }
  );
  if (!res.ok) return { text: "" };
  return res.json();
}

export async function askQuestionStream(
  question,
  docName,
  chatHistory,
  onToken,
  onDone,
  topK = 5,
  orgId = null,
) {
  // Always get a fresh token from session — don't rely on stale tokenRef
  const session = await getSession();
  const token   = session?.access_token || "dev-token";

  const headers = {
    "Content-Type": "application/json",
    Authorization:  `Bearer ${token}`,
  };
  if (orgId) headers["X-Org-Id"] = orgId;

  const res = await fetch(`${AI_URL}/query/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      question,
      top_k:        topK,
      doc_name:     docName || null,
      chat_history: Array.isArray(chatHistory) ? chatHistory.slice(-6) : [],
    }),
  });

  if (!res.ok) throw new Error(await res.text());

  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer    = "";

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
      } catch { continue; }
    }
  }
}