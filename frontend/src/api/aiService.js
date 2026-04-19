const AUTH_URL = "http://localhost:3000";
const AI_URL   = "http://localhost:8000";

export async function getSession() {
  const res = await fetch(`${AUTH_URL}/`, { credentials: "include" });
  return res.json();
}

export async function uploadDocument(file) {
  const session = await getSession();
  const token = session?.access_token;  

  console.log("TOKEN:", token); // debug

  if (!token) {
    throw new Error("No token found. User not authenticated.");
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

export async function askQuestion(question, topK = 10) {
  const session = await getSession();

  console.log("SESSION:", session);

  if (!session || !session.access_token) {
    throw new Error("User not authenticated. Please login again.");
  }

  const token = session.access_token;

  const res = await fetch(`${AI_URL}/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      question: question,
      top_k: Number(topK),
    }),
  });

  if (!res.ok) {
    const error = await res.text();
    console.error("QUERY ERROR:", error);
    throw new Error(error);
  }

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