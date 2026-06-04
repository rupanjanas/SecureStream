const AUTH_URL = import.meta.env.VITE_BACKEND_URL;

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

export async function getOrgMembers() {
  const res = await fetch(`${AUTH_URL}/org/members`, {
    credentials: "include"
  });
  if (!res.ok) return { members: [] };
  return res.json();
}

export async function getOnlineMembers() {
  const res = await fetch(`${AUTH_URL}/org/online`, {
    credentials: "include"
  });
  if (!res.ok) return { online: [] };
  return res.json();
}

export async function pingPresence() {
  try {
    await fetch(`${AUTH_URL}/org/presence`, {
      method: "POST",
      credentials: "include"
    });
  } catch { /* silent — presence ping should never crash the app */ }
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

export async function getMemberships() {
  const res = await fetch(`${AUTH_URL}/org/memberships`, {
    credentials: "include"
  });
  return res.json();
}

export async function selectWorkspace(mode, orgId = null) {
  const res = await fetch(`${AUTH_URL}/org/select`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, orgId })
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}