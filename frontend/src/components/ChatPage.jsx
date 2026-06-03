import { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import Navbar from "../components/Navbar";
import { askQuestionStream } from "../api/aiService";
import { generateInvite, sendEmailInvite } from "../api/orgService";

const AUTH_URL = import.meta.env.VITE_BACKEND_URL;

// ── Shared prompt log key ─────────────────────────────────────────────────────
function getOrgChatKey(orgId) {
  return `org_chat_${orgId}`;
}

function loadOrgMessages(orgId) {
  try {
    const saved = localStorage.getItem(getOrgChatKey(orgId));
    return saved ? JSON.parse(saved) : [];
  } catch {
    return [];
  }
}

function saveOrgMessages(orgId, messages) {
  try {
    const settled = messages.filter((m) => !m.streaming && m.content);
    localStorage.setItem(getOrgChatKey(orgId), JSON.stringify(settled));
  } catch { /* storage full */ }
}

// ── Message component ─────────────────────────────────────────────────────────
function Message({ msg, isOrg }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : "flex-row"}`}>
      {!isUser && (
        <div className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5 ${
          isOrg ? "bg-emerald-600" : "bg-[#185FA5]"
        }`}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="white">
            <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
          </svg>
        </div>
      )}
      {isUser && msg.senderName && (
        <div className="flex flex-col items-end gap-0.5 justify-end mb-1 flex-shrink-0">
          <span className="text-xs text-gray-400">{msg.senderName}</span>
        </div>
      )}
      <div className="max-w-[75%] flex flex-col gap-2">
        <div className={`rounded-2xl px-4 py-3 text-sm leading-relaxed ${
          isUser
            ? isOrg
              ? "bg-emerald-600 text-white rounded-tr-sm"
              : "bg-[#185FA5] text-white rounded-tr-sm"
            : "bg-white border border-gray-100 text-gray-800 rounded-tl-sm"
        }`}>
          {msg.content}
          {msg.streaming && (
            <span className="inline-block w-1 h-3 bg-gray-400 ml-0.5 animate-pulse rounded"/>
          )}
        </div>
        {msg.sources?.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <p className="text-xs text-gray-400">Sources:</p>
            {msg.sources.map((s, i) => (
              <div key={i}
                className="text-xs bg-blue-50 border border-blue-100 rounded-lg px-3 py-2 text-gray-600 leading-relaxed">
                {s}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Invite panel ──────────────────────────────────────────────────────────────
function InvitePanel({ onClose }) {
  const [inviteUrl, setInviteUrl]   = useState("");
  const [email, setEmail]           = useState("");
  const [generating, setGenerating] = useState(false);
  const [sending, setSending]       = useState(false);
  const [copied, setCopied]         = useState(false);
  const [emailSent, setEmailSent]   = useState(false);
  const [error, setError]           = useState("");

  const handleGenerate = async () => {
    setGenerating(true);
    setError("");
    try {
      const data = await generateInvite();
      setInviteUrl(data.inviteUrl);
    } catch {
      setError("Failed to generate invite link.");
    } finally {
      setGenerating(false);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(inviteUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleEmailSend = async () => {
    if (!email.trim() || !inviteUrl) return;
    setSending(true);
    setError("");
    try {
      await sendEmailInvite(email.trim(), inviteUrl);
      setEmailSent(true);
      setEmail("");
    } catch {
      setError("Failed to send email.");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md mx-4 p-6">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-base font-semibold text-gray-900">Invite to workspace</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-lg">✕</button>
        </div>

        <div className="mb-5">
          <p className="text-xs text-gray-500 mb-3">Generate an invite link to share with your team:</p>
          {!inviteUrl ? (
            <button
              onClick={handleGenerate}
              disabled={generating}
              className="w-full py-2.5 text-sm rounded-xl bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40 transition-colors"
            >
              {generating ? "Generating..." : "Generate invite link"}
            </button>
          ) : (
            <div className="flex gap-2">
              <input
                type="text"
                value={inviteUrl}
                readOnly
                className="flex-1 border border-gray-200 rounded-xl px-3 py-2 text-xs text-gray-600 bg-gray-50 focus:outline-none"
              />
              <button
                onClick={handleCopy}
                className={`px-4 py-2 text-xs rounded-xl transition-colors ${
                  copied
                    ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                    : "bg-gray-100 text-gray-700 hover:bg-gray-200"
                }`}
              >
                {copied ? "Copied!" : "Copy"}
              </button>
            </div>
          )}
        </div>

        {inviteUrl && (
          <div className="border-t border-gray-100 pt-5">
            <p className="text-xs text-gray-500 mb-3">Or send directly via email:</p>
            {emailSent ? (
              <p className="text-sm text-emerald-600 font-medium">✓ Invite sent!</p>
            ) : (
              <div className="flex gap-2">
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleEmailSend()}
                  placeholder="colleague@company.com"
                  className="flex-1 border border-gray-200 rounded-xl px-3 py-2 text-xs focus:outline-none focus:border-emerald-400 transition-colors"
                />
                <button
                  onClick={handleEmailSend}
                  disabled={sending || !email.trim()}
                  className="px-4 py-2 text-xs rounded-xl bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40 transition-colors"
                >
                  {sending ? "Sending..." : "Send"}
                </button>
              </div>
            )}
          </div>
        )}

        {error && <p className="text-xs text-red-500 mt-3">{error}</p>}
        <p className="text-xs text-gray-400 mt-4">
          Anyone with this link can join your workspace as a member.
        </p>
      </div>
    </div>
  );
}

// ── Main ChatPage ─────────────────────────────────────────────────────────────
export default function ChatPage({ user, orgId, orgName, mode }) {
  const isOrg    = mode === "org";
  const navigate = useNavigate();

  // FIX: read orgId from session via ref so send() always has the latest value
  // even if the prop arrives after initial render.
  const orgIdRef = useRef(orgId || null);
  useEffect(() => {
    // Keep ref in sync if prop updates (e.g. workspace switch)
    orgIdRef.current = orgId || null;
  }, [orgId]);

  // ── Messages — org mode uses persistent shared log, personal uses session ──
  const [messages, setMessages] = useState(() => {
    if (isOrg && orgId) return loadOrgMessages(orgId);
    try {
      const saved = sessionStorage.getItem("personal_chat");
      return saved ? JSON.parse(saved) : [];
    } catch { return []; }
  });

  // Clear stuck streaming messages on mount
  useEffect(() => {
    setMessages((m) => m.filter((msg) => !msg.streaming && msg.content));
  }, []);

  // Persist messages
  useEffect(() => {
    if (isOrg && orgId) {
      saveOrgMessages(orgId, messages);
    } else {
      try {
        const settled = messages.filter((m) => !m.streaming && m.content);
        sessionStorage.setItem("personal_chat", JSON.stringify(settled));
      } catch { /* storage full */ }
    }
  }, [messages, isOrg, orgId]);

  const [input, setInput]           = useState("");
  const [loading, setLoading]       = useState(false);
  const [showInvite, setShowInvite] = useState(false);
  const bottomRef                   = useRef(null);
  const textareaRef                 = useRef(null);

  const userName = user?.given_name || user?.email?.split("@")[0] || "You";

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const send = async () => {
    const question = input.trim();
    if (!question || loading) return;

    setMessages((m) => [
      ...m.filter((msg) => !msg.streaming),
      { role: "user", content: question, senderName: userName },
      { role: "assistant", content: "", streaming: true }
    ]);
    setInput("");
    setLoading(true);

    try {
      // FIX: pass orgIdRef.current so the backend scopes the query to the
      // correct org — previously null was always passed here, which meant
      // newly-invited members could not query org documents.
      await askQuestionStream(
        question,
        null,         // no specific doc — searches all
        [],           // chat history not threaded in global chat
        (token) => {
          setMessages((m) => {
            const updated = [...m];
            const last    = updated[updated.length - 1];
            updated[updated.length - 1] = { ...last, content: last.content + token };
            return updated;
          });
        },
        (sources) => {
          setMessages((m) => {
            const updated = [...m];
            updated[updated.length - 1] = {
              ...updated[updated.length - 1],
              streaming: false,
              sources:   sources || []
            };
            return updated;
          });
          setLoading(false);
        },
        3,                   // topK
        orgIdRef.current,    // FIX: was hardcoded null
      );
    } catch (err) {
      setMessages((m) => {
        const updated = [...m];
        updated[updated.length - 1] = {
          ...updated[updated.length - 1],
          content:   `Something went wrong: ${err.message}`,
          streaming: false
        };
        return updated;
      });
      setLoading(false);
    }
  };

  const handleKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const clearChat = () => {
    if (isOrg && orgId) localStorage.removeItem(getOrgChatKey(orgId));
    else sessionStorage.removeItem("personal_chat");
    setMessages([]);
  };

  const accentBtn = isOrg ? "bg-emerald-600 hover:bg-emerald-700" : "bg-[#185FA5] hover:bg-[#0C447C]";

  return (
    <div className="min-h-screen flex flex-col bg-gray-50 font-sans">
      <Navbar user={user} />

      {showInvite && <InvitePanel onClose={() => setShowInvite(false)} />}

      <main className="flex-1 max-w-3xl mx-auto w-full px-6 py-6 flex flex-col"
        style={{ height: "calc(100vh - 57px)" }}>

        {/* Header */}
        <div className="flex items-center justify-between mb-4 flex-shrink-0">
          <div>
            <button onClick={() => navigate("/dashboard")}
              className="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1 mb-1">
              ← Dashboard
            </button>
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-bold text-gray-900">Ask your documents</h1>
              {isOrg && (
                <span className="text-xs bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full font-medium">
                  {orgName}
                </span>
              )}
            </div>
            {isOrg && (
              <p className="text-xs text-gray-400 mt-0.5">
                Shared workspace — prompts visible to all members
              </p>
            )}
          </div>

          <div className="flex items-center gap-2">
            {isOrg && (
              <button
                onClick={() => setShowInvite(true)}
                className="px-3 py-1.5 text-xs rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition-colors flex items-center gap-1.5"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                  stroke="white" strokeWidth="2" strokeLinecap="round">
                  <path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/>
                  <circle cx="9" cy="7" r="4"/>
                  <line x1="19" y1="8" x2="19" y2="14"/>
                  <line x1="22" y1="11" x2="16" y2="11"/>
                </svg>
                Invite
              </button>
            )}

            <button
              onClick={() => navigate("/upload")}
              className="px-3 py-1.5 text-xs rounded-lg border border-gray-200 hover:bg-white text-gray-600 transition-colors flex items-center gap-1.5"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <line x1="12" y1="5" x2="12" y2="19"/>
                <line x1="5" y1="12" x2="19" y2="12"/>
              </svg>
              Upload doc
            </button>

            <button
              onClick={clearChat}
              className="text-xs text-gray-400 hover:text-red-400 transition-colors px-2 py-1.5"
            >
              Clear
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 bg-white border border-gray-100 rounded-2xl p-5 flex flex-col gap-5 overflow-y-auto mb-4">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <div className={`w-12 h-12 rounded-xl flex items-center justify-center mb-3 ${
                isOrg ? "bg-emerald-50" : "bg-blue-50"
              }`}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                  stroke={isOrg ? "#0F6E56" : "#185FA5"} strokeWidth="2" strokeLinecap="round">
                  <circle cx="11" cy="11" r="8"/>
                  <line x1="21" y1="21" x2="16.65" y2="16.65"/>
                </svg>
              </div>
              <p className="text-sm text-gray-500 font-medium mb-1">
                {isOrg ? `Ask about ${orgName}'s documents` : "Ask your documents"}
              </p>
              <p className="text-xs text-gray-400 max-w-xs">
                {isOrg
                  ? "Questions and answers are shared with your whole team."
                  : "Upload a document first, then ask anything about it."}
              </p>
            </div>
          )}

          {messages.map((msg, i) => (
            <Message key={i} msg={msg} isOrg={isOrg} />
          ))}

          {loading && !messages[messages.length - 1]?.streaming && (
            <div className="flex gap-3 flex-row">
              <div className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ${
                isOrg ? "bg-emerald-600" : "bg-[#185FA5]"
              }`}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="white">
                  <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
                </svg>
              </div>
              <div className="bg-white border border-gray-100 rounded-2xl rounded-tl-sm px-4 py-3.5 flex gap-1.5 items-center">
                {[0, 150, 300].map((delay) => (
                  <span key={delay}
                    className="w-1.5 h-1.5 bg-gray-300 rounded-full animate-bounce"
                    style={{ animationDelay: `${delay}ms` }}
                  />
                ))}
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="flex-shrink-0">
          <div className="flex gap-2">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKey}
              placeholder={isOrg
                ? `Ask about ${orgName}'s documents...`
                : "Ask a question about your documents..."}
              rows={1}
              className={`flex-1 resize-none rounded-xl border border-gray-200 px-4 py-3 text-sm focus:outline-none transition-colors bg-white ${
                isOrg ? "focus:border-emerald-400" : "focus:border-[#185FA5]"
              }`}
              style={{ minHeight: "48px", maxHeight: "120px" }}
            />
            <button
              onClick={send}
              disabled={!input.trim() || loading}
              className={`px-4 rounded-xl text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center ${accentBtn}`}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13"/>
                <polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </button>
          </div>
          <p className="text-xs text-gray-400 text-center mt-2">
            Enter to send · Shift+Enter for new line · Answers grounded in your documents only
          </p>
        </div>
      </main>
    </div>
  );
}