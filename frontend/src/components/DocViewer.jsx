import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Document, Page, pdfjs } from "react-pdf";
import Navbar from "../components/Navbar";
import { retrieveFile } from "../utils/filestore";
import {
  getAnnotations,
  createAnnotation,
  updateAnnotation,
  deleteAnnotation,
  toggleShareAnnotation,
  getOrgMembers,
  getOnlineMembers,
  pingPresence,
} from "../api/orgService";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { askQuestionStream, getDocumentText } from "../api/aiService";
pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

const AUTH_URL          = import.meta.env.VITE_BACKEND_URL;
const HIGHLIGHT_COLOR   = "#FEF08A";
const ANNOTATION_COLORS = ["#FCD34D", "#86EFAC", "#93C5FD", "#F9A8D4", "#C4B5FD"];

function chatKey(docName)    { return `chat_v2_${docName}`; }
function sourcesKey(docName) { return `sources_v2_${docName}`; }

function loadFromStorage(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch { return fallback; }
}

function saveToStorage(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (error) { void error; }
}

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlightText(text, phrases) {
  if (!phrases.length) return [{ text, highlight: false }];
  const pattern = phrases.map(escapeRegex).join("|");
  try {
    const regex = new RegExp(`(${pattern})`, "gi");
    return text.split(regex).map((part) => ({
      text: part,
      highlight: phrases.some((p) => p.toLowerCase() === part.toLowerCase()),
    }));
  } catch {
    return [{ text, highlight: false }];
  }
}

function getInitials(email) {
  if (!email) return "?";
  const name  = email.split("@")[0];
  const parts = name.split(/[._-]/);
  return parts.length >= 2
    ? (parts[0][0] + parts[1][0]).toUpperCase()
    : name.slice(0, 2).toUpperCase();
}

function getAvatarColor(email) {
  const palettes = [
    "bg-blue-100 text-blue-700",
    "bg-violet-100 text-violet-700",
    "bg-emerald-100 text-emerald-700",
    "bg-amber-100 text-amber-700",
    "bg-pink-100 text-pink-700",
    "bg-teal-100 text-teal-700",
  ];
  let h = 0;
  for (const c of (email || "")) h = c.charCodeAt(0) + h * 31;
  return palettes[Math.abs(h) % palettes.length];
}

function fmtTime(ts) {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function MemberAvatar({ member, isOnline, isCurrent }) {
  const [showTip, setShowTip] = useState(false);
  return (
    <div
      className="relative"
      onMouseEnter={() => setShowTip(true)}
      onMouseLeave={() => setShowTip(false)}
    >
      <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold border-2 ${
        getAvatarColor(member.email)
      } ${isCurrent ? "border-[#185FA5]" : "border-white"}`}>
        {getInitials(member.email)}
      </div>
      <span className={`absolute bottom-0 right-0 w-2 h-2 rounded-full border border-white ${
        isOnline ? "bg-emerald-400" : "bg-gray-300"
      }`} />
      {showTip && (
        <div className="absolute top-9 right-0 z-50 bg-gray-900 text-white text-xs rounded-lg px-2.5 py-1.5 whitespace-nowrap shadow-lg">
          <p className="font-medium">{member.email?.split("@")[0]}</p>
          <p className="text-gray-400 capitalize">{member.role} · {isOnline ? "Online" : "Offline"}</p>
        </div>
      )}
    </div>
  );
}

function SourcesPanel({ sourcesHistory, isPDF, pageRefs, onClose }) {
  const groups        = [...sourcesHistory].reverse();
  const totalPassages = sourcesHistory.reduce((n, g) => n + (g.passages?.length || 0), 0);
  const [collapsed, setCollapsed] = useState({});
  const toggle = (idx) => setCollapsed((s) => ({ ...s, [idx]: !s[idx] }));

  return (
    <div className="flex flex-col bg-white border-l border-gray-200 z-40 shadow-xl flex-shrink-0"
      style={{ width: "320px", height: "100%" }}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 bg-amber-50 flex-shrink-0">
        <div>
          <p className="text-sm font-semibold text-gray-900">Source history</p>
          <p className="text-xs text-gray-500">
            {groups.length} question{groups.length !== 1 ? "s" : ""} · {totalPassages} passage{totalPassages !== 1 ? "s" : ""}
          </p>
        </div>
        <button onClick={onClose}
          className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {groups.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full pb-10 text-center px-6">
            <div className="w-11 h-11 bg-amber-50 rounded-2xl flex items-center justify-center mb-3">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#b45309" strokeWidth="1.8" strokeLinecap="round">
                <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="9" y1="15" x2="15" y2="15"/>
                <line x1="9" y1="11" x2="15" y2="11"/>
              </svg>
            </div>
            <p className="text-xs font-medium text-gray-600 mb-1">No sources yet</p>
            <p className="text-xs text-gray-400 leading-relaxed">
              Ask a question in the chat. Matched passages will appear here.
            </p>
          </div>
        ) : (
          <div className="py-2">
            {groups.map((group, gIdx) => {
              const isCollapsed = collapsed[gIdx];
              const passages    = group.passages || [];
              return (
                <div key={group.id || gIdx} className="mb-0.5">
                  <button onClick={() => toggle(gIdx)}
                    className="w-full flex items-start gap-2.5 px-4 py-2.5 hover:bg-gray-50 transition-colors text-left">
                    <div className="w-4 h-4 rounded bg-amber-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                      <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="#b45309" strokeWidth="3" strokeLinecap="round"
                        className={`transition-transform duration-200 ${isCollapsed ? "-rotate-90" : ""}`}>
                        <polyline points="6 9 12 15 18 9"/>
                      </svg>
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium text-gray-800 leading-snug line-clamp-2">{group.question}</p>
                      <p className="text-xs text-gray-400 mt-0.5">
                        {fmtTime(group.timestamp)} · {passages.length} source{passages.length !== 1 ? "s" : ""}
                      </p>
                    </div>
                  </button>
                  {!isCollapsed && (
                    <div className="px-3 pb-2 flex flex-col gap-1.5">
                      {passages.length === 0 ? (
                        <p className="text-xs text-gray-400 px-2 py-1.5 italic">No passages found.</p>
                      ) : (
                        passages.map((p, pIdx) => (
                          <div key={pIdx}
                            onClick={() => {
                              if (!isPDF) {
                                const marks = document.querySelectorAll("mark");
                                if (marks[pIdx]) marks[pIdx].scrollIntoView({ behavior: "smooth", block: "center" });
                              } else {
                                const pageEl = pageRefs.current[p.page_number || 1];
                                if (pageEl) pageEl.scrollIntoView({ behavior: "smooth", block: "start" });
                              }
                            }}
                            className="ml-6 bg-white border border-amber-100 hover:border-amber-300 hover:bg-amber-50 rounded-lg px-3 py-2.5 cursor-pointer transition-all group">
                            <div className="flex items-center justify-between mb-1">
                              <span className="text-xs text-gray-500 truncate flex items-center gap-1 min-w-0">
                                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="flex-shrink-0">
                                  <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                                  <polyline points="14 2 14 8 20 8"/>
                                </svg>
                                <span className="truncate">{p.doc_name}</span>
                              </span>
                              <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium flex-shrink-0 ml-2 ${
                                p.similarity > 0.7 ? "bg-green-50 text-green-700"
                                : p.similarity > 0.4 ? "bg-amber-50 text-amber-700"
                                : "bg-gray-100 text-gray-500"
                              }`}>
                                {p.similarity > 0 ? `${Math.round(p.similarity * 100)}%` : "kw"}
                              </span>
                            </div>
                            <p className="text-xs text-gray-600 leading-relaxed line-clamp-2 group-hover:line-clamp-none transition-all">
                              {p.passage.slice(0, 180)}{p.passage.length > 180 ? "…" : ""}
                            </p>
                            {p.page_number && (
                              <p className="text-xs text-gray-400 mt-1.5 flex items-center gap-1">
                                <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <rect x="3" y="3" width="18" height="18" rx="2"/>
                                </svg>
                                Page {p.page_number}
                              </p>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  )}
                  {gIdx < groups.length - 1 && <div className="mx-4 border-b border-gray-100"/>}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {groups.length > 0 && (
        <div className="flex-shrink-0 border-t border-gray-100 px-4 py-2.5 bg-gray-50 flex items-center justify-between">
          <span className="text-xs text-gray-400">Showing all sessions for this doc</span>
          <button
            onClick={() => setCollapsed(groups.reduce((acc, _, i) => ({ ...acc, [i]: true }), {}))}
            className="text-xs text-gray-400 hover:text-gray-600 transition-colors">
            Collapse all
          </button>
        </div>
      )}
    </div>
  );
}

function InviteModal({ orgName, onClose, token }) {
  const [copied, setCopied]               = useState(false);
  const [inviteEmail, setInviteEmail]     = useState("");
  const [sendingInvite, setSendingInvite] = useState(false);
  const [inviteSent, setInviteSent]       = useState(false);
  const [inviteError, setInviteError]     = useState("");
  const [inviteLink, setInviteLink]       = useState("");
  const [linkLoading, setLinkLoading]     = useState(true);  // start true
  const [linkError, setLinkError]         = useState("");

  const fetchInviteLink = useCallback(() => {
  if (!token) {
    setLinkError("Authentication not ready. Please close and reopen.");
    setLinkLoading(false);
    return;
  }
  fetch(`${import.meta.env.VITE_BACKEND_URL}/org/invite`, {
    method:      "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Authorization:  `Bearer ${token}`,
    },
  })
    .then((r) => {
      if (!r.ok) throw new Error(`Server returned ${r.status}`);
      return r.json();
    })
    .then((d) => {
      if (!d.inviteUrl) throw new Error("No inviteUrl in response");
      setInviteLink(d.inviteUrl);
      setLinkError("");
      setLinkLoading(false);
    })
    .catch((err) => {
      console.error("[InviteModal] fetch failed:", err.message);
      setLinkError("Could not generate invite link. Try closing and reopening.");
      setLinkLoading(false);
    });
}, [token]);

  useEffect(() => {
    const id = window.setTimeout(fetchInviteLink, 0);
    return () => window.clearTimeout(id);
  }, [fetchInviteLink]);

  const handleCopyLink = () => {
    if (!inviteLink) return;
    navigator.clipboard.writeText(inviteLink).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleSendInvite = async () => {
    if (!inviteEmail.trim() || !inviteEmail.includes("@")) {
      setInviteError("Please enter a valid email address.");
      return;
    }
    if (!inviteLink) {
      setInviteError("Invite link not ready yet. Please wait.");
      return;
    }
    setInviteError("");
    setSendingInvite(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_BACKEND_URL}/org/invite/email`, {
        method:  "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ email: inviteEmail.trim(), inviteUrl: inviteLink }),
      });
      if (!res.ok) throw new Error("Server error");
      setInviteSent(true);
      setInviteEmail("");
      setTimeout(() => setInviteSent(false), 3000);
    } catch {
      setInviteError("Failed to send invite. Please try again.");
    } finally {
      setSendingInvite(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 bg-emerald-50">
          <div>
            <p className="text-sm font-semibold text-gray-900">Invite to {orgName}</p>
            <p className="text-xs text-gray-500">Share the link — invited users land directly in the workspace</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 rounded-lg p-1 hover:bg-gray-100 transition-colors">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>

        <div className="px-5 py-5 flex flex-col gap-5">

          {/* ── Shareable link ── */}
          <div>
            <p className="text-xs font-medium text-gray-700 mb-2">Shareable invite link</p>

            {linkLoading ? (
              <div className="flex items-center gap-2 border border-gray-200 rounded-xl px-3 py-2.5 bg-gray-50">
                <svg className="animate-spin flex-shrink-0" width="14" height="14" viewBox="0 0 24 24"
                  fill="none" stroke="#6b7280" strokeWidth="2.5" strokeLinecap="round">
                  <path d="M21 12a9 9 0 11-6.219-8.56"/>
                </svg>
                <span className="text-xs text-gray-400">Generating link…</span>
              </div>
            ) : linkError ? (
              <div className="border border-red-200 rounded-xl px-3 py-2.5 bg-red-50">
                <p className="text-xs text-red-500">{linkError}</p>
                <button
                  onClick={fetchInviteLink}
                  className="text-xs text-red-500 underline mt-1">
                  Retry
                </button>
              </div>
            ) : (
              <div className="flex gap-2">
                <div className="flex-1 border border-gray-200 rounded-xl px-3 py-2 bg-gray-50 text-xs text-gray-600 truncate select-all font-mono">
                  {inviteLink}
                </div>
                <button
                  onClick={handleCopyLink}
                  className={`px-3 py-2 rounded-xl text-xs font-medium transition-all flex-shrink-0 ${
                    copied
                      ? "bg-emerald-100 text-emerald-700 border border-emerald-200"
                      : "bg-gray-100 text-gray-700 hover:bg-gray-200 border border-gray-200"
                  }`}>
                  {copied ? "✓ Copied" : "Copy"}
                </button>
              </div>
            )}
            <p className="text-xs text-gray-400 mt-1.5">
              Anyone with this link joins <strong>{orgName}</strong> and lands directly in the workspace.
            </p>
          </div>

          {/* ── Email ── */}
          <div>
            <div className="flex items-center gap-3 mb-4">
              <div className="flex-1 h-px bg-gray-100"/>
              <span className="text-xs text-gray-400">or send by email</span>
              <div className="flex-1 h-px bg-gray-100"/>
            </div>
            <p className="text-xs font-medium text-gray-700 mb-2">Send email invitation</p>
            <input
              type="email"
              value={inviteEmail}
              onChange={(e) => { setInviteEmail(e.target.value); setInviteError(""); }}
              onKeyDown={(e) => e.key === "Enter" && handleSendInvite()}
              placeholder="colleague@company.com"
              className="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-emerald-400 transition-colors mb-2"
            />
            {inviteError && <p className="text-xs text-red-500 mb-2">{inviteError}</p>}
            {inviteSent  && <p className="text-xs text-emerald-600 mb-2">✓ Invite sent successfully!</p>}
            <button
              onClick={handleSendInvite}
              disabled={!inviteEmail.trim() || sendingInvite || !inviteLink}
              className="w-full py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-40 text-white text-xs font-medium rounded-xl transition-colors">
              {sendingInvite ? "Sending…" : "Send invite"}
            </button>
          </div>

        </div>
      </div>
    </div>
  );
}

function ManageMembersModal({ members, onlineSet, userEmail, onClose, onRemoveMember, onChangeRole }) {
  const [searchQuery, setSearchQuery] = useState("");
  const [removingId, setRemovingId]   = useState(null);
  const [updatingId, setUpdatingId]   = useState(null);

  const filtered = members.filter(
    (m) =>
      m.email?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      m.role?.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 bg-emerald-50 flex-shrink-0">
          <div>
            <p className="text-sm font-semibold text-gray-900">Manage members</p>
            <p className="text-xs text-gray-500">{members.length} member{members.length !== 1 ? "s" : ""} · {onlineSet.size} online</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 rounded-lg p-1 hover:bg-gray-100 transition-colors">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
        <div className="px-5 py-3 border-b border-gray-100 flex-shrink-0">
          <div className="relative">
            <svg className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <input type="text" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search members…"
              className="w-full pl-8 pr-3 py-2 border border-gray-200 rounded-xl text-sm focus:outline-none focus:border-emerald-400 transition-colors"/>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-3 flex flex-col gap-2">
          {filtered.length === 0 ? (
            <p className="text-xs text-gray-400 text-center py-8">No members found.</p>
          ) : (
            filtered.map((member) => {
              const isCurrentUser = member.email === userEmail;
              const isOnline      = onlineSet.has(member.user_sub);
              return (
                <div key={member.user_sub}
                  className={`flex items-center gap-3 p-3 rounded-xl border transition-colors ${
                    isCurrentUser ? "border-emerald-200 bg-emerald-50" : "border-gray-100 hover:bg-gray-50"
                  }`}>
                  <div className="relative flex-shrink-0">
                    <div className={`w-9 h-9 rounded-full flex items-center justify-center text-xs font-semibold ${getAvatarColor(member.email)}`}>
                      {getInitials(member.email)}
                    </div>
                    <span className={`absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full border-2 border-white ${isOnline ? "bg-emerald-400" : "bg-gray-300"}`}/>
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900 truncate">
                      {member.email?.split("@")[0]}
                      {isCurrentUser && <span className="ml-1.5 text-xs text-emerald-600 font-normal">(you)</span>}
                    </p>
                    <p className="text-xs text-gray-400 truncate">{member.email}</p>
                  </div>
                  <span className={`text-xs px-2 py-0.5 rounded-full flex-shrink-0 ${isOnline ? "bg-emerald-50 text-emerald-700" : "bg-gray-100 text-gray-500"}`}>
                    {isOnline ? "Online" : "Offline"}
                  </span>
                  {!isCurrentUser ? (
                    <select value={member.role || "member"}
                      onChange={(e) => {
                        setUpdatingId(member.user_sub);
                        onChangeRole(member, e.target.value).finally(() => setUpdatingId(null));
                      }}
                      disabled={updatingId === member.user_sub}
                      className="border border-gray-200 rounded-lg px-2 py-1 text-xs bg-white focus:outline-none focus:border-emerald-400 text-gray-700 flex-shrink-0 disabled:opacity-50">
                      <option value="member">Member</option>
                      <option value="admin">Admin</option>
                      <option value="viewer">Viewer</option>
                    </select>
                  ) : (
                    <span className="text-xs text-gray-500 px-2 py-1 bg-gray-100 rounded-lg flex-shrink-0 capitalize">
                      {member.role || "member"}
                    </span>
                  )}
                  {!isCurrentUser && (
                    <button
                      onClick={() => {
                        setRemovingId(member.user_sub);
                        onRemoveMember(member).finally(() => setRemovingId(null));
                      }}
                      disabled={removingId === member.user_sub}
                      className="flex-shrink-0 text-xs text-red-400 hover:text-red-600 hover:bg-red-50 px-2 py-1 rounded-lg transition-colors disabled:opacity-40">
                      {removingId === member.user_sub ? "…" : (
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                          <polyline points="3 6 5 6 21 6"/>
                          <path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/>
                          <path d="M10 11v6"/><path d="M14 11v6"/>
                        </svg>
                      )}
                    </button>
                  )}
                </div>
              );
            })
          )}
        </div>
        <div className="px-5 py-3 border-t border-gray-100 bg-gray-50 flex-shrink-0">
          <p className="text-xs text-gray-400 text-center">
            Only admins can remove members or change roles. Changes take effect immediately.
          </p>
        </div>
      </div>
    </div>
  );
}

export default function DocViewerPage({ user, mode, orgName }) {
  const location = useLocation();
  const navigate = useNavigate();

  const {
    docName  = "",
    docText  = "",
    file_url: fileUrl = null,
    file:    stateFile = null,
  } = location.state || {};

  const isOrg = mode === "org";
  const isPDF = docName.toLowerCase().endsWith(".pdf");

  const CHAT_KEY    = chatKey(docName);
  const SOURCES_KEY = sourcesKey(docName);

  const tokenRef = useRef(null);
  const [tokenReady, setTokenReady] = useState(false);

  useEffect(() => {
    fetch(`${AUTH_URL}/`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => { tokenRef.current = d.access_token || "dev-token"; setTokenReady(true); })
      .catch(()  => { tokenRef.current = "dev-token"; setTokenReady(true); });
  }, []);

  const [resolvedFileUrl, setResolvedFileUrl] = useState(fileUrl || null);
  const [manualFile, setManualFile]           = useState(null);
  const blobUrlRef  = useRef(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    if (!isPDF || resolvedFileUrl || !tokenReady) return;
    const fetchFileUrl = async () => {
      try {
        const res = await fetch(
          `${AUTH_URL}/documents/file-url?doc_name=${encodeURIComponent(docName)}`,
          { headers: { Authorization: `Bearer ${tokenRef.current}` } }
        );
        if (res.ok) {
          const data = await res.json();
          if (data.file_url) setResolvedFileUrl(data.file_url);
        }
      } catch (err) {
        console.warn("[PDF] Could not fetch file_url from backend:", err);
      }
    };
    fetchFileUrl();
  }, [isPDF, resolvedFileUrl, tokenReady, docName]);

  const handleManualFileSelect = (e) => {
    const file = e.target.files?.[0];
    if (!file || file.type !== "application/pdf") return;
    if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
    blobUrlRef.current = URL.createObjectURL(file);
    setManualFile(file);
  };

  const pdfFile = useMemo(() => {
    if (!isPDF) return null;
    if (resolvedFileUrl) return { url: resolvedFileUrl };
    if (manualFile && blobUrlRef.current) return { url: blobUrlRef.current };
    if (!blobUrlRef.current) {
      const fileObj = (stateFile instanceof File ? stateFile : null) ?? retrieveFile();
      if (fileObj) blobUrlRef.current = URL.createObjectURL(fileObj);
    }
    return blobUrlRef.current ? { url: blobUrlRef.current } : null;
  }, [isPDF, resolvedFileUrl, stateFile, manualFile]);

  useEffect(() => {
    return () => {
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, []);

  const [messages, setMessages] = useState(() =>
    loadFromStorage(chatKey(docName), []).filter((m) => !m.streaming && m.content)
  );
  const [sourcesHistory, setSourcesHistory] = useState(() =>
    loadFromStorage(sourcesKey(docName), [])
  );
  const [chatHistory, setChatHistory]           = useState([]);
  const [input, setInput]                       = useState("");
  const [loading, setLoading]                   = useState(false);
  const [highlights, setHighlights]             = useState([]);
  const [highlightedPages, setHighlightedPages] = useState({});
  const [numPages, setNumPages]                 = useState(null);
  const [fetchedText, setFetchedText]           = useState("");
  const [annotations, setAnnotations]           = useState([]);
  const [activeAnnotation, setActiveAnnotation] = useState(null);
  const [newNote, setNewNote]                   = useState("");
  const [selectedText, setSelectedText]         = useState("");
  const [noteColor, setNoteColor]               = useState(ANNOTATION_COLORS[0]);
  const [showNotePanel, setShowNotePanel]       = useState(false);
  const [savingNote, setSavingNote]             = useState(false);
  const [editingId, setEditingId]               = useState(null);
  const [editNote, setEditNote]                 = useState("");
  const [deletingId, setDeletingId]             = useState(null);
  const [sharingId, setSharingId]               = useState(null);
  const [members, setMembers]                   = useState([]);
  const [onlineSet, setOnlineSet]               = useState(new Set());
  const [showSourcesPanel, setShowSourcesPanel] = useState(false);
  const [showInviteModal, setShowInviteModal]   = useState(false);
  const [showMembersModal, setShowMembersModal] = useState(false);

  const pageRefs  = useRef({});
  const bottomRef = useRef(null);

  const userEmail      = user?.email || "dev@securestream.local";
  const sourcePassages = sourcesHistory.length > 0
    ? (sourcesHistory[sourcesHistory.length - 1].passages || [])
    : [];

  useEffect(() => {
    const settled = messages.filter((m) => !m.streaming && m.content);
    if (settled.length) saveToStorage(CHAT_KEY, settled);
  }, [messages, CHAT_KEY]);

  useEffect(() => {
    if (sourcesHistory.length) saveToStorage(SOURCES_KEY, sourcesHistory);
  }, [sourcesHistory, SOURCES_KEY]);

  useEffect(() => {
  if (!docName) {
    navigate("/dashboard", { replace: true });
  }
}, [docName, navigate]);

  useEffect(() => {
    if (!docName || !tokenReady) return;
    const t = setTimeout(async () => {
      try {
        const res = await getDocumentText(docName, tokenRef.current);
        setFetchedText(res.text || "");
      } catch {
        setFetchedText("");
      }
    }, 400);
    return () => clearTimeout(t);
  }, [docName, tokenReady]);

  useEffect(() => {
    if (!docName || !tokenReady) return;
    const t = setTimeout(() => {
      getAnnotations(docName, tokenRef.current)
        .then((d) => setAnnotations(Array.isArray(d) ? d : []))
        .catch(()  => setAnnotations([]));
    }, 400);
    return () => clearTimeout(t);
  }, [docName, tokenReady]);

  useEffect(() => {
    if (!isOrg) return;
    const fetchAll = async () => {
      try {
        const [mData, oData] = await Promise.all([getOrgMembers(), getOnlineMembers()]);
        setMembers(mData.members || []);
        setOnlineSet(new Set((oData.online || []).map((o) => o.user_sub)));
      } catch {
        setMembers([]);
        setOnlineSet(new Set());
      }
    };
    fetchAll();
    const iv = setInterval(fetchAll, 30000);
    return () => clearInterval(iv);
  }, [isOrg]);

  useEffect(() => {
    if (!isOrg) return;
    pingPresence();
    const iv = setInterval(pingPresence, 60000);
    return () => clearInterval(iv);
  }, [isOrg]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const sendAnnotationEvent = () => {};

  const handleTextSelect = () => {
    if (isPDF) return;
    const sel = window.getSelection()?.toString().trim();
    if (sel && sel.length > 3) { setSelectedText(sel); setShowNotePanel(true); }
  };

  const handleSaveNote = async () => {
    if (!newNote.trim() || !selectedText) return;
    setSavingNote(true);
    try {
      const ann = await createAnnotation({
        doc_name: docName, selected_text: selectedText,
        note: newNote.trim(), color: noteColor, is_shared: false,
      }, tokenRef.current);
      setAnnotations((a) => [...a, ann]);
      sendAnnotationEvent("create", ann);
      setNewNote(""); setSelectedText(""); setShowNotePanel(false);
    } catch (err) { console.error("Annotation error:", err); }
    finally { setSavingNote(false); }
  };

  const handleEditNote = async (ann) => {
    if (!editNote.trim()) return;
    try {
      const updated = await updateAnnotation(ann.id, {
        doc_name: ann.doc_name, selected_text: ann.selected_text,
        note: editNote.trim(), color: ann.color,
      }, tokenRef.current);
      setAnnotations((prev) => prev.map((a) => a.id === ann.id ? { ...a, note: updated.note } : a));
      if (activeAnnotation?.id === ann.id) setActiveAnnotation((a) => ({ ...a, note: updated.note }));
      sendAnnotationEvent("update", { ...ann, note: updated.note });
      setEditingId(null); setEditNote("");
    } catch (err) { console.error("Edit error:", err); }
  };

  const handleDeleteAnnotation = async (ann) => {
    setDeletingId(ann.id);
    try {
      await deleteAnnotation(ann.id, tokenRef.current);
      setAnnotations((prev) => prev.filter((a) => a.id !== ann.id));
      if (activeAnnotation?.id === ann.id) setActiveAnnotation(null);
      sendAnnotationEvent("delete", { id: ann.id });
    } catch (err) { console.error("Delete error:", err); }
    finally { setDeletingId(null); }
  };

  const handleToggleShare = async (ann) => {
    setSharingId(ann.id);
    try {
      const updated = await toggleShareAnnotation(ann.id, !ann.is_shared, tokenRef.current);
      setAnnotations((prev) => prev.map((a) => a.id === ann.id ? { ...a, is_shared: updated.is_shared } : a));
      if (activeAnnotation?.id === ann.id) setActiveAnnotation((a) => ({ ...a, is_shared: updated.is_shared }));
      sendAnnotationEvent("update", { ...ann, is_shared: updated.is_shared });
    } catch (err) { console.error("Share error:", err); }
    finally { setSharingId(null); }
  };

  const handleRemoveMember = async (member) => {
    await fetch(`${import.meta.env.VITE_BACKEND_URL}/org/members/${member.user_sub}`, {
      method: "DELETE", headers: { Authorization: `Bearer ${tokenRef.current}` },
    });
    setMembers((prev) => prev.filter((m) => m.user_sub !== member.user_sub));
  };

  const handleChangeRole = async (member, newRole) => {
    await fetch(`${import.meta.env.VITE_BACKEND_URL}/org/members/${member.user_sub}/role`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${tokenRef.current}` },
      body: JSON.stringify({ role: newRole }),
    });
    setMembers((prev) => prev.map((m) => m.user_sub === member.user_sub ? { ...m, role: newRole } : m));
  };

  const send = async () => {
    const question = input.trim();
    if (!question || loading) return;

    const newHistory = [...chatHistory, { role: "user", content: question }];
    setChatHistory(newHistory);
    setMessages((m) => [
      ...m.filter((msg) => !msg.streaming),
      { role: "user", content: question },
      { role: "assistant", content: "", streaming: true },
    ]);
    setInput("");
    setLoading(true);
    setHighlights([]);
    setHighlightedPages({});

    try {
      await askQuestionStream(
        question,
        docName,
        newHistory,
        (token) => {
          setMessages((m) => {
            const updated = [...m];
            const last    = updated[updated.length - 1];
            updated[updated.length - 1] = { ...last, content: last.content + token };
            return updated;
          });
        },
        (sources, passages) => {
          setChatHistory((h) => [
            ...h,
            { role: "assistant", content: passages.length > 0 ? "..." : "Not found" }
          ]);
          setMessages((m) => {
            const updated = [...m];
            updated[updated.length - 1] = {
              ...updated[updated.length - 1],
              streaming: false,
              sources,
            };
            return updated;
          });

          const newGroup = {
            id:        `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
            question,
            passages:  passages || [],
            timestamp: Date.now(),
          };
          setSourcesHistory((prev) => [...prev, newGroup]);

          const phrases = [...new Set(
            (passages || [])
              .sort((a, b) => b.similarity - a.similarity)
              .map((p) => p.passage.slice(0, 120))
          )];
          setHighlights(phrases);

          if (isPDF && passages?.length > 0) {
            const sorted = [...passages].sort((a, b) => b.similarity - a.similarity);
            const pageH  = {};
            sorted.forEach((p) => {
              const pg = p.page_number || 1;
              if (!pageH[pg]) pageH[pg] = [];
              pageH[pg].push(p.passage.slice(0, 120));
            });
            setHighlightedPages(pageH);
            setTimeout(() => {
              const el = pageRefs.current[sorted[0].page_number || 1];
              if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
            }, 400);
          }

          setLoading(false);
        },
        3,
        null
      );
    } catch (err) {
      setMessages((m) => {
        const updated = [...m];
        updated[updated.length - 1] = {
          ...updated[updated.length - 1],
          content:   `Error: ${err.message}`,
          streaming: false,
        };
        return updated;
      });
      setLoading(false);
    }
  };

  const safeAnnotations   = Array.isArray(annotations) ? annotations : [];
  const myAnnotations     = safeAnnotations.filter((a) => a.user_email === userEmail);
  const sharedAnnotations = safeAnnotations.filter((a) => a.is_shared && a.user_email !== userEmail);
  const displayText       = docText || fetchedText;
  const parts             = highlightText(displayText, highlights);
  const onlineCount       = onlineSet.size;
  const totalSourceCount  = sourcesHistory.reduce((n, g) => n + (g.passages?.length || 0), 0);
  const accentBtn         = isOrg ? "bg-emerald-600 hover:bg-emerald-700" : "bg-[#185FA5] hover:bg-[#0C447C]";
  const accentFocus       = isOrg ? "focus:border-emerald-400" : "focus:border-[#185FA5]";

  return (
    <div className="min-h-screen flex flex-col bg-gray-50 font-sans">
      <Navbar user={user} />

      {showInviteModal && tokenReady && (
      <InviteModal orgName={orgName} token={tokenRef.current} onClose={() => setShowInviteModal(false)} />
      )}
      {showMembersModal && (
        <ManageMembersModal
          members={members} onlineSet={onlineSet} userEmail={userEmail}
          onClose={() => setShowMembersModal(false)}
          onRemoveMember={handleRemoveMember} onChangeRole={handleChangeRole}
        />
      )}

      <main className="flex overflow-hidden" style={{ height: "calc(100vh - 57px)" }}>

        <div className="flex-1 flex flex-col overflow-hidden border-r border-gray-100 bg-white min-w-0">

          <div className={`flex items-center justify-between px-5 py-2.5 border-b flex-shrink-0 ${
            isOrg ? "bg-gradient-to-r from-emerald-50 to-teal-50 border-emerald-200" : "bg-white border-gray-100"
          }`}>
            <div className="flex items-center gap-3 min-w-0">
              <button onClick={() => navigate("/dashboard")}
                className="text-xs text-gray-400 hover:text-gray-600 transition-colors flex-shrink-0">
                ← Dashboard
              </button>
              <span className="text-gray-200">|</span>
              {isOrg && (
                <span className="text-xs bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full font-medium flex-shrink-0 flex items-center gap-1">
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                    <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/>
                    <circle cx="9" cy="7" r="4"/>
                    <path d="M23 21v-2a4 4 0 00-3-3.87"/>
                    <path d="M16 3.13a4 4 0 010 7.75"/>
                  </svg>
                  {orgName}
                </span>
              )}
              <span className="text-sm font-medium text-gray-700 truncate">{docName}</span>
              <span className={`text-xs px-2 py-0.5 rounded-full flex-shrink-0 ${isPDF ? "bg-red-50 text-red-600" : "bg-blue-50 text-blue-600"}`}>
                {isPDF ? "PDF" : "TXT"}
              </span>
            </div>

            <div className="flex items-center gap-2 flex-shrink-0">
              {!isPDF && highlights.length > 0 && (
                <span className="text-xs bg-yellow-100 text-yellow-700 px-2 py-1 rounded-lg">
                  {highlights.length} highlights
                </span>
              )}
              {sourcesHistory.length > 0 && (
                <button
                  onClick={() => setShowSourcesPanel((v) => !v)}
                  className={`text-xs px-2.5 py-1 rounded-lg flex items-center gap-1.5 transition-colors border ${
                    showSourcesPanel
                      ? "bg-amber-100 text-amber-800 border-amber-300"
                      : "bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100"
                  }`}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                    <polyline points="14 2 14 8 20 8"/>
                  </svg>
                  {totalSourceCount} source{totalSourceCount !== 1 ? "s" : ""}
                </button>
              )}
              <span className="text-xs text-gray-400">
                {myAnnotations.length} note{myAnnotations.length !== 1 ? "s" : ""}
              </span>
              {isOrg && members.length > 0 && (
                <div className="flex items-center gap-2 pl-2 border-l border-emerald-200">
                  <span className="text-xs text-emerald-600 font-medium">{onlineCount} online</span>
                  <div className="flex -space-x-1.5">
                    {members.slice(0, 5).map((m) => (
                      <MemberAvatar key={m.user_sub} member={m}
                        isOnline={onlineSet.has(m.user_sub)} isCurrent={m.email === userEmail}/>
                    ))}
                    {members.length > 5 && (
                      <div className="w-7 h-7 rounded-full bg-gray-100 border-2 border-white flex items-center justify-center text-xs font-medium text-gray-500">
                        +{members.length - 5}
                      </div>
                    )}
                  </div>
                  <button onClick={() => setShowMembersModal(true)}
                    className="text-xs text-emerald-600 hover:text-emerald-800 hover:bg-emerald-100 px-2 py-1 rounded-lg transition-colors border border-emerald-200 flex items-center gap-1">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                      <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/>
                      <circle cx="9" cy="7" r="4"/>
                      <line x1="23" y1="11" x2="23" y2="17"/>
                      <line x1="20" y1="14" x2="26" y2="14"/>
                    </svg>
                    Manage
                  </button>
                  <button onClick={() => setShowInviteModal(true)}
                    className={`text-xs text-white px-2 py-1 rounded-lg transition-colors flex items-center gap-1 ${accentBtn}`}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round">
                      <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                    </svg>
                    Invite
                  </button>
                </div>
              )}
              {isOrg && members.length === 0 && (
                <button onClick={() => setShowInviteModal(true)}
                  className={`text-xs text-white px-2.5 py-1 rounded-lg transition-colors flex items-center gap-1 ${accentBtn}`}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round">
                    <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                  </svg>
                  Invite
                </button>
              )}
            </div>
          </div>

          {sharedAnnotations.length > 0 && (
            <div className="px-5 py-2 border-b border-gray-100 bg-blue-50 flex items-center gap-2 flex-shrink-0 overflow-x-auto">
              <span className="text-xs text-blue-600 font-medium flex-shrink-0">Shared by team:</span>
              {sharedAnnotations.map((ann) => (
                <button key={ann.id}
                  onClick={() => setActiveAnnotation(activeAnnotation?.id === ann.id ? null : ann)}
                  style={{ borderColor: ann.color, backgroundColor: ann.color + "30" }}
                  className="text-xs border rounded-lg px-2 py-1 text-gray-700 hover:opacity-80 transition-opacity flex-shrink-0">
                  {ann.user_email?.split("@")[0]} · "{ann.selected_text.slice(0, 20)}..."
                </button>
              ))}
            </div>
          )}

          <div
            onMouseUp={!isPDF ? handleTextSelect : undefined}
            className="flex-1 overflow-y-auto"
            style={{ position: "relative" }}
          >
            {activeAnnotation && (
              <div style={{ borderLeftColor: activeAnnotation.color }}
                className="border-l-4 mx-5 mt-4 bg-gray-50 rounded-r-xl px-4 py-3">
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-gray-400 mb-0.5">
                      {activeAnnotation.user_email === userEmail ? "Your note"
                        : `Note by ${activeAnnotation.user_email?.split("@")[0]}`}
                      {activeAnnotation.is_shared && <span className="ml-2 text-blue-500">· shared with org</span>}
                    </p>
                    <p className="text-xs text-gray-500 italic mb-1">
                      "{activeAnnotation.selected_text?.slice(0, 80)}{activeAnnotation.selected_text?.length > 80 ? "..." : ""}"
                    </p>
                    {editingId === activeAnnotation.id ? (
                      <div className="flex gap-2 mt-1">
                        <input type="text" value={editNote} onChange={(e) => setEditNote(e.target.value)}
                          onKeyDown={(e) => e.key === "Enter" && handleEditNote(activeAnnotation)}
                          className="flex-1 border border-gray-200 rounded-lg px-2 py-1 text-sm focus:outline-none focus:border-[#185FA5]"
                          autoFocus/>
                        <button onClick={() => handleEditNote(activeAnnotation)}
                          className="text-xs px-2 py-1 bg-[#185FA5] text-white rounded-lg">Save</button>
                        <button onClick={() => { setEditingId(null); setEditNote(""); }}
                          className="text-xs px-2 py-1 bg-gray-100 text-gray-600 rounded-lg">Cancel</button>
                      </div>
                    ) : (
                      <p className="text-sm text-gray-900">{activeAnnotation.note}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-1 ml-3 flex-shrink-0">
                    {activeAnnotation.user_email === userEmail && editingId !== activeAnnotation.id && (
                      <button onClick={() => { setEditingId(activeAnnotation.id); setEditNote(activeAnnotation.note); }}
                        className="text-xs text-gray-400 hover:text-gray-600 px-1.5 py-1 rounded hover:bg-gray-100">Edit</button>
                    )}
                    {activeAnnotation.user_email === userEmail && (
                      <button onClick={() => handleDeleteAnnotation(activeAnnotation)}
                        disabled={deletingId === activeAnnotation.id}
                        className="text-xs text-red-400 hover:text-red-600 px-1.5 py-1 rounded hover:bg-red-50">
                        {deletingId === activeAnnotation.id ? "..." : "Delete"}
                      </button>
                    )}
                    <button onClick={() => setActiveAnnotation(null)}
                      className="text-xs text-gray-400 hover:text-gray-600 px-1 py-1 rounded">✕</button>
                  </div>
                </div>
                {activeAnnotation.user_email === userEmail && editingId !== activeAnnotation.id && (
                  <button onClick={() => handleToggleShare(activeAnnotation)}
                    disabled={sharingId === activeAnnotation.id}
                    className={`mt-2 text-xs px-3 py-1 rounded-lg transition-colors ${
                      activeAnnotation.is_shared
                        ? "bg-blue-50 text-blue-600 hover:bg-blue-100"
                        : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                    }`}>
                    {sharingId === activeAnnotation.id ? "Updating..."
                      : activeAnnotation.is_shared ? "Shared · click to make private"
                      : "Private · click to share with org"}
                  </button>
                )}
              </div>
            )}

            {isPDF ? (
              <div className="flex justify-center py-4 px-4">
                {pdfFile ? (
                  <Document
                    file={pdfFile}
                    onLoadSuccess={({ numPages: n }) => setNumPages(n)}
                    onLoadError={(err) => console.error("PDF load error:", err)}
                  >
                    {Array.from({ length: numPages || 1 }, (_, i) => {
                      const pageNum = i + 1;
                      return (
                        <div key={pageNum} ref={(el) => { pageRefs.current[pageNum] = el; }} className="relative mb-4">
                          <Page
                            pageNumber={pageNum}
                            width={Math.min(600, window.innerWidth - 60)}
                            className="border border-gray-100 rounded-lg overflow-hidden"
                            onRenderSuccess={() => {
                              if (!highlightedPages[pageNum]?.length) return;
                              const pageEl = pageRefs.current[pageNum];
                              if (!pageEl) return;
                              pageEl.querySelectorAll(".react-pdf__Page__textContent span").forEach((span) => {
                                const matched = highlightedPages[pageNum].some((phrase) =>
                                  span.textContent.toLowerCase().includes(phrase.toLowerCase().slice(0, 40))
                                );
                                if (matched) {
                                  span.style.backgroundColor = "rgba(254, 240, 138, 0.7)";
                                  span.style.borderRadius    = "2px";
                                }
                              });
                            }}
                          />
                        </div>
                      );
                    })}
                  </Document>
                ) : (
                  <div className="flex flex-col items-center justify-center pt-20 text-center px-6">
                    <div className="w-14 h-14 bg-gray-100 rounded-2xl flex items-center justify-center mb-4">
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth="1.5" strokeLinecap="round">
                        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                        <polyline points="14 2 14 8 20 8"/>
                      </svg>
                    </div>
                    <p className="text-sm text-gray-500 mb-1">
                      {tokenReady ? "PDF could not be loaded from storage." : "Loading PDF…"}
                    </p>
                    <p className="text-xs text-gray-400 mb-4">
                      {tokenReady
                        ? "The file URL could not be resolved."
                        : "Fetching document URL from storage…"}
                    </p>
                    {tokenReady && (
                      <>
                        <input
                          type="file"
                          accept="application/pdf"
                          ref={fileInputRef}
                          onChange={handleManualFileSelect}
                          className="hidden"
                        />
                        <button
                          onClick={() => fileInputRef.current?.click()}
                          className={`px-4 py-2 text-white text-xs rounded-xl transition-colors mb-2 ${accentBtn}`}>
                          Open PDF from device
                        </button>
                        <p className="text-xs text-gray-400">
                          Or re-upload the document from the dashboard to fix storage permanently.
                        </p>
                      </>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div className="px-8 py-6 max-w-prose mx-auto">
                {displayText ? (
                  <>
                    {myAnnotations.length > 0 && (
                      <div className="flex flex-wrap gap-2 mb-6 pb-4 border-b border-gray-100">
                        <span className="text-xs text-gray-400 w-full">Your notes:</span>
                        {myAnnotations.map((ann) => (
                          <button key={ann.id}
                            onClick={() => setActiveAnnotation(activeAnnotation?.id === ann.id ? null : ann)}
                            style={{ borderColor: ann.color }}
                            className="text-xs border rounded-lg px-2 py-1 text-gray-600 hover:bg-gray-50 flex items-center gap-1.5">
                            <span style={{ backgroundColor: ann.color }} className="w-2 h-2 rounded-full inline-block"/>
                            "{ann.selected_text.slice(0, 25)}..."
                            {ann.is_shared && <span className="text-blue-400 ml-1">shared</span>}
                          </button>
                        ))}
                      </div>
                    )}
                    <p className="text-sm text-gray-800 leading-8 whitespace-pre-wrap select-text">
                      {parts.map((part, i) =>
                        part.highlight ? (
                          <mark key={i} style={{ backgroundColor: HIGHLIGHT_COLOR }} className="rounded px-0.5">{part.text}</mark>
                        ) : (
                          <span key={i}>{part.text}</span>
                        )
                      )}
                    </p>
                  </>
                ) : (
                  <div className="flex flex-col items-center justify-center pt-20 text-center">
                    <div className="w-14 h-14 bg-gray-100 rounded-2xl flex items-center justify-center mb-4">
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth="1.5" strokeLinecap="round">
                        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                        <polyline points="14 2 14 8 20 8"/>
                      </svg>
                    </div>
                    <p className="text-sm text-gray-400 mb-3">Loading document...</p>
                  </div>
                )}
              </div>
            )}
          </div>

          {showNotePanel && (
            <div className="flex-shrink-0 border-t border-gray-200 bg-white px-5 py-4">
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs font-medium text-gray-700">
                  Add note for:{" "}
                  <span className="text-gray-500 italic">
                    "{selectedText.slice(0, 50)}{selectedText.length > 50 ? "..." : ""}"
                  </span>
                </p>
                <button onClick={() => { setShowNotePanel(false); setSelectedText(""); }}
                  className="text-xs text-gray-400 hover:text-gray-600">Cancel</button>
              </div>
              <div className="flex items-center gap-2 mb-3">
                <span className="text-xs text-gray-400">Color:</span>
                {ANNOTATION_COLORS.map((c) => (
                  <button key={c} onClick={() => setNoteColor(c)} style={{ backgroundColor: c }}
                    className={`w-5 h-5 rounded-full border-2 transition-transform ${noteColor === c ? "border-gray-600 scale-110" : "border-transparent"}`}/>
                ))}
              </div>
              <div className="flex gap-2">
                <input type="text" value={newNote} onChange={(e) => setNewNote(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleSaveNote()}
                  placeholder="Write your note..."
                  className="flex-1 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-[#185FA5] transition-colors"
                  autoFocus/>
                <button onClick={handleSaveNote} disabled={!newNote.trim() || savingNote}
                  className={`px-4 py-2 text-xs rounded-xl text-white disabled:opacity-40 transition-colors ${accentBtn}`}>
                  {savingNote ? "Saving..." : "Save note"}
                </button>
              </div>
            </div>
          )}
        </div>

        {showSourcesPanel && (
          <SourcesPanel sourcesHistory={sourcesHistory} isPDF={isPDF}
            pageRefs={pageRefs} onClose={() => setShowSourcesPanel(false)}/>
        )}

        <div className="w-96 flex flex-col bg-white flex-shrink-0 border-l border-gray-100">
          <div className={`px-4 py-3 border-b flex-shrink-0 ${isOrg ? "bg-emerald-50 border-emerald-100" : "bg-white border-gray-100"}`}>
            <div className="flex items-center justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <p className="text-sm font-semibold text-gray-900">Ask AI</p>
                  <button
                    onClick={() => {
                      localStorage.removeItem(CHAT_KEY);
                      localStorage.removeItem(SOURCES_KEY);
                      setMessages([]); setSourcesHistory([]); setChatHistory([]);
                      setHighlights([]); setHighlightedPages({});
                    }}
                    className="text-xs text-gray-400 hover:text-red-400 transition-colors">
                    Clear
                  </button>
                </div>
                <p className="text-xs text-gray-400">
                  {isPDF ? "Searches embedded content" : "Highlights relevant passages"}
                </p>
              </div>
              {isOrg && (
                <span className="text-xs bg-emerald-100 text-emerald-700 px-2 py-1 rounded-lg font-medium">
                  Shared workspace
                </span>
              )}
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-4">
            {messages.length === 0 && (
              <div className="text-center mt-10">
                <div className={`w-10 h-10 rounded-xl flex items-center justify-center mx-auto mb-3 ${isOrg ? "bg-emerald-50" : "bg-blue-50"}`}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                    stroke={isOrg ? "#0F6E56" : "#185FA5"} strokeWidth="2" strokeLinecap="round">
                    <circle cx="11" cy="11" r="8"/>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"/>
                  </svg>
                </div>
                <p className="text-xs text-gray-400 leading-relaxed px-4">
                  {isOrg ? "Ask anything about this shared document."
                    : "Ask anything. Matching passages are highlighted automatically."}
                </p>
              </div>
            )}

            {messages.map((msg, i) => (
              <div key={i} className={`flex gap-2 ${msg.role === "user" ? "flex-row-reverse" : ""}`}>
                {msg.role === "assistant" && (
                  <div className={`w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5 ${isOrg ? "bg-emerald-600" : "bg-[#185FA5]"}`}>
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="white">
                      <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
                    </svg>
                  </div>
                )}
                <div
                  className={`rounded-2xl px-3 py-2.5 text-xs leading-relaxed max-w-[82%] ${
                    msg.role === "user"
                      ? isOrg ? "bg-emerald-600 text-white rounded-tr-sm" : "bg-[#185FA5] text-white rounded-tr-sm"
                      : "bg-gray-50 border border-gray-100 text-gray-800 rounded-tl-sm"
                  }`}
                  style={{ wordBreak: "break-word", overflowWrap: "anywhere" }}
                >
                  {msg.content}
                  {msg.streaming && <span className="inline-block w-1 h-3 bg-gray-400 ml-0.5 animate-pulse rounded"/>}
                </div>
              </div>
            ))}

            {loading && !messages[messages.length - 1]?.streaming && (
              <div className="flex gap-2">
                <div className={`w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0 ${isOrg ? "bg-emerald-600" : "bg-[#185FA5]"}`}>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="white">
                    <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
                  </svg>
                </div>
                <div className="bg-gray-50 border border-gray-100 rounded-2xl rounded-tl-sm px-3 py-2.5 flex gap-1">
                  {[0, 150, 300].map((d) => (
                    <span key={d} className="w-1.5 h-1.5 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }}/>
                  ))}
                </div>
              </div>
            )}
            <div ref={bottomRef}/>
          </div>

          {sourcePassages.length > 0 && (
            <div className="flex-shrink-0 border-t border-gray-100 px-4 py-3 bg-gray-50">
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs font-medium text-gray-500">Latest sources ({sourcePassages.length})</p>
                <button onClick={() => setShowSourcesPanel((v) => !v)}
                  className={`text-xs transition-colors flex items-center gap-1 ${
                    showSourcesPanel ? "text-amber-700 font-medium" : "text-gray-400 hover:text-amber-600"
                  }`}>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/>
                    <polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
                  </svg>
                  {showSourcesPanel ? "Close history" : `All history (${sourcesHistory.length})`}
                </button>
              </div>
              <div className="flex flex-col gap-2 max-h-36 overflow-y-auto">
                {sourcePassages.map((p, i) => (
                  <div key={i}
                    onClick={() => {
                      if (!isPDF) {
                        const marks = document.querySelectorAll("mark");
                        if (marks[i]) marks[i].scrollIntoView({ behavior: "smooth", block: "center" });
                      } else {
                        const el = pageRefs.current[p.page_number || 1];
                        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
                      }
                    }}
                    className="bg-white border border-yellow-200 rounded-lg px-3 py-2 cursor-pointer hover:border-yellow-400 transition-colors">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium text-gray-600 truncate">{p.doc_name}</span>
                      <span className="text-xs text-yellow-600 ml-2 flex-shrink-0">
                        {p.similarity > 0 ? `${Math.round(p.similarity * 100)}% match` : "keyword match"}
                      </span>
                    </div>
                    <p className="text-xs text-gray-500 leading-relaxed line-clamp-2">{p.passage.slice(0, 120)}...</p>
                    {p.page_number && <p className="text-xs text-gray-400 mt-1">Page {p.page_number}</p>}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="flex-shrink-0 px-4 py-3 border-t border-gray-100">
            <div className="flex gap-2">
              <input type="text" value={input} onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && send()}
                placeholder={isOrg ? "Ask about this shared document..." : "Ask about this document..."}
                className={`flex-1 border border-gray-200 rounded-xl px-3 py-2 text-xs focus:outline-none transition-colors ${accentFocus}`}/>
              <button onClick={send} disabled={!input.trim() || loading}
                className={`px-3 rounded-xl text-white disabled:opacity-40 transition-colors ${accentBtn}`}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round">
                  <line x1="22" y1="2" x2="11" y2="13"/>
                  <polygon points="22 2 15 22 11 13 2 9 22 2"/>
                </svg>
              </button>
            </div>
            <p className="text-xs text-gray-400 text-center mt-1.5">
              {isPDF ? "AI searches embedded content" : "Select text to annotate · Enter to ask"}
            </p>
          </div>
        </div>

      </main>
    </div>
  );
}