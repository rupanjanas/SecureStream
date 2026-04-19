const AUTH_URL = "http://localhost:3000";
const AI_URL   = "http://localhost:8000";

async function getToken() {
  const res = await fetch(`${AUTH_URL}/`, { credentials: "include" });
  const session = await res.json();

  console.log("SESSION:", session); // debug

  return session?.access_token;
}

export async function createOrg(name) {
  const res = await fetch(`${AUTH_URL}/org/create`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name })
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getMyOrg() {
  const res = await fetch(`${AUTH_URL}/org/me`, { credentials: "include" });
  return res.json();
}

export async function generateInvite() {
  const res = await fetch(`${AUTH_URL}/org/invite`, {
    method: "POST",
    credentials: "include"
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function sendEmailInvite(email, inviteUrl) {
  const res = await fetch(`${AUTH_URL}/org/invite/email`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, inviteUrl })
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getAnnotations(docName) {
  const token = await getToken();

  if (!token) throw new Error("No token");

  const res = await fetch(
    `${AI_URL}/annotations/${encodeURIComponent(docName)}`,
    {
      headers: {
        Authorization: `Bearer ${token}`
      }
    }
  );

  if (!res.ok) throw new Error(await res.text());

  return res.json();
}

export async function createAnnotation(data) {
  const token = await getToken();

  if (!token) throw new Error("No token");

  const res = await fetch(`${AI_URL}/annotations`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify(data)
  });

  if (!res.ok) throw new Error(await res.text());

  return res.json();
}

export async function toggleShareAnnotation(id, isShared) {
  const token = await getToken();

  if (!token) throw new Error("No token");

  const res = await fetch(`${AI_URL}/annotations/${id}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({ is_shared: isShared })
  });

  if (!res.ok) throw new Error(await res.text());

  return res.json();
}