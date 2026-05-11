const AUTH_URL = import.meta.env.VITE_BACKEND_URL;
const AI_URL = import.meta.env.VITE_AI_SERVICE_URL;

export async function getSession() {
  const res = await fetch(`${AUTH_URL}/`, { credentials: "include" });

  if (!res.ok) return null;

  const data = await res.json();

  // ✅ ADD THIS CHECK
  if (!data.isAuthenticated || !data.access_token) {
    return null;
  }

  return data;
}

export async function uploadDocument(file) {
 const session = await getSession();

if (!session || !session.isAuthenticated || !session.access_token) {
  throw new Error("User not authenticated.");
}

const token = session.access_token;
if (!token) {
  throw new Error("User not authenticated.");
}

  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${AI_URL}/ingest`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
    },
    body: form,
  });

  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// Streaming version — calls onToken for each word, onDone when finished
export async function askQuestionStream(question, onToken, onDone, topK = 3, currentDocName) {
  const session = await getSession();
  console.log("SESSION:", session);
if (!session || !session.access_token) {
  throw new Error("User not authenticated.");
}

const token = session.access_token;

  const payload = JSON.parse(atob(token.split(".")[1]));
  const isExpired = payload.exp * 1000 < Date.now();

  if (isExpired) {
    window.location.href = `${AUTH_URL}/login`;
    return;
  }

  const res = await fetch(`${AI_URL}/query/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,   // ✅ FIXED
    },
    body: JSON.stringify({ question,doc_name: currentDocName, top_k: topK })
  });

  if (!res.ok) throw new Error(await res.text());

  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

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

        if (data.token) onToken(data.token);
        if (data.done) onDone(data.sources || [], data.source_passages || []);
      } catch {
        continue;
      }
    }
  }
}

export async function listDocuments(token) {
  const res = await fetch(`${AI_URL}/documents`, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });

  if (!res.ok) {
    const err = await res.text();
    console.error("LIST DOCS ERROR:", err);
    throw new Error(err);
  }

  return res.json();
}

export async function getDocumentText(docName, token) {
  const res = await fetch(`${AI_URL}/documents/${encodeURIComponent(docName)}/text`, {
    headers: {
      Authorization: `Bearer ${token}`
    }
  });

  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getHealth() {
  try {
    const res = await fetch(`${AI_URL}/health`);
    return res.json();
  } catch {
    return { status: "error", db: "error" };
  }
}