import { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";

const AUTH_URL = import.meta.env.VITE_BACKEND_URL;

const features = [
  {
    title: "Upload any document",
    desc: "Drop in a PDF or text file. SecureStream extracts and indexes the content automatically.",
    bg: "bg-blue-50",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#185FA5" strokeWidth="2" strokeLinecap="round">
        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
        <line x1="12" y1="18" x2="12" y2="12"/>
        <line x1="9" y1="15" x2="15" y2="15"/>
      </svg>
    ),
  },
  {
    title: "Ask in plain English",
    desc: "Type your question exactly as you would ask a colleague. No keywords, no search syntax.",
    bg: "bg-violet-50",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#534AB7" strokeWidth="2" strokeLinecap="round">
        <circle cx="11" cy="11" r="8"/>
        <line x1="21" y1="21" x2="16.65" y2="16.65"/>
      </svg>
    ),
  },
  {
    title: "See exactly where it came from",
    desc: "Every answer links back to the passage in the document it was pulled from. No guessing.",
    bg: "bg-amber-50",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#b45309" strokeWidth="2" strokeLinecap="round">
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
      </svg>
    ),
  },
  {
    title: "Works on long documents",
    desc: "Built for the papers, reports, and manuals that are too long to read end to end.",
    bg: "bg-emerald-50",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0F6E56" strokeWidth="2" strokeLinecap="round">
        <line x1="8" y1="6" x2="21" y2="6"/>
        <line x1="8" y1="12" x2="21" y2="12"/>
        <line x1="8" y1="18" x2="21" y2="18"/>
        <line x1="3" y1="6" x2="3.01" y2="6"/>
        <line x1="3" y1="12" x2="3.01" y2="12"/>
        <line x1="3" y1="18" x2="3.01" y2="18"/>
      </svg>
    ),
  },
];

const steps = [
  { step: "01", label: "Upload your document", detail: "PDF or plain text" },
  { step: "02", label: "Ask your question", detail: "In plain English" },
  { step: "03", label: "Get the answer", detail: "With the source highlighted" },
];

export default function LandingPage() {
  const [user, setUser] = useState(null);
  const [dropOpen, setDropOpen] = useState(false);
  const dropRef = useRef(null);
  const navigate = useNavigate();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("error") === "auth_failed") {
      console.warn("Authentication failed");
    }
    fetch(`${AUTH_URL}`, { credentials: "include" })
      .then((r) => r.json())
      .then((data) => { if (data.isAuthenticated) setUser(data.user); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    function handleClick(e) {
      if (dropRef.current && !dropRef.current.contains(e.target)) setDropOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const initials = user
    ? (user.given_name?.[0] ?? "") + (user.family_name?.[0] ?? "")
    : "";

  return (
    <div className="min-h-screen flex flex-col bg-white text-gray-900 font-sans">

      {/* ── Nav ── */}
      <nav className="sticky top-0 z-50 flex items-center justify-between px-8 py-3.5 border-b border-gray-100 bg-white">
        <div className="flex items-center gap-2 text-base font-semibold">
          <div className="w-7 h-7 bg-[#185FA5] rounded-lg flex items-center justify-center">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="white">
              <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
            </svg>
          </div>
          SecureStream
        </div>

        <div className="flex items-center gap-2">
          {user ? (
            <div className="relative" ref={dropRef}>
              <button
                onClick={() => setDropOpen((o) => !o)}
                className="w-8 h-8 rounded-full bg-blue-100 text-blue-800 text-xs font-semibold flex items-center justify-center border border-gray-200 uppercase"
              >
                {initials || user.email?.[0]?.toUpperCase()}
              </button>
              {dropOpen && (
                <div className="absolute right-0 top-10 w-52 bg-white border border-gray-100 rounded-xl shadow-sm overflow-hidden z-50">
                  <div className="px-4 py-3 border-b border-gray-100">
                    <p className="text-sm font-semibold truncate">
                      {user.given_name ? `${user.given_name} ${user.family_name ?? ""}` : user.email}
                    </p>
                    <span className="text-xs text-gray-500 truncate block">{user.email}</span>
                  </div>
                  <button onClick={() => { setDropOpen(false); navigate("/dashboard"); }}
                    className="block w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50">
                    Dashboard
                  </button>
                  <button onClick={() => { setDropOpen(false); navigate("/upload"); }}
                    className="block w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50">
                    Upload document
                  </button>
                  <button onClick={() => { setDropOpen(false); navigate("/chat"); }}
                    className="block w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50">
                    Ask AI
                  </button>
                  <div className="border-t border-gray-100">
                    <a href={`${AUTH_URL}/logout`}
                      className="block px-4 py-2.5 text-sm text-red-600 hover:bg-gray-50">
                      Sign out
                    </a>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <>
              <a href={`${AUTH_URL}/login`}
                className="px-4 py-1.5 text-sm rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors">
                Log in
              </a>
              <a href={`${AUTH_URL}/login`}
                className="px-4 py-1.5 text-sm rounded-lg bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors">
                Get started
              </a>
            </>
          )}
        </div>
      </nav>

      <main className="flex-1">

        {/* ── Hero ── */}
        <section className="max-w-2xl mx-auto text-center px-6 pt-20 pb-14">
          <div className="inline-flex items-center gap-1.5 bg-blue-50 text-blue-700 text-xs font-medium px-3 py-1 rounded-full mb-6">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="#185FA5">
              <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
            </svg>
            AI-powered document search
          </div>

          <h1 className="text-4xl font-bold leading-tight mb-4 text-gray-900">
            Stop skimming.<br/>Just ask.
          </h1>

          <p className="text-gray-500 text-base mb-8 leading-relaxed max-w-lg mx-auto">
            Upload a research paper, report, or any long document. Ask a question in plain English.
            SecureStream finds the exact answer and shows you where it came from.
          </p>

          <div className="flex gap-3 justify-center">
            {user ? (
              <button onClick={() => navigate("/dashboard")}
                className="px-6 py-2.5 text-sm rounded-lg bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors">
                Go to dashboard →
              </button>
            ) : (
              <>
                <a href={`${AUTH_URL}/login`}
                  className="px-6 py-2.5 text-sm rounded-lg bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors">
                  Try it free
                </a>
                <button onClick={() => navigate("/onboarding")}
                  className="px-6 py-2.5 text-sm rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors">
                  See how it works
                </button>
              </>
            )}
          </div>
        </section>

        {/* ── How it works ── */}
        <section className="max-w-2xl mx-auto px-6 pb-14">
          <div className="bg-gray-50 rounded-2xl p-6 flex flex-col sm:flex-row items-center justify-between gap-4">
            {steps.map((s, i) => (
              <div key={s.step} className="flex items-center gap-4 flex-1">
                <div className="flex flex-col items-center sm:items-start gap-0.5 flex-1">
                  <span className="text-xs font-mono text-gray-400">{s.step}</span>
                  <span className="text-sm font-semibold text-gray-800">{s.label}</span>
                  <span className="text-xs text-gray-400">{s.detail}</span>
                </div>
                {i < steps.length - 1 && (
                  <svg className="hidden sm:block flex-shrink-0 text-gray-300" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <line x1="5" y1="12" x2="19" y2="12"/>
                    <polyline points="12 5 19 12 12 19"/>
                  </svg>
                )}
              </div>
            ))}
          </div>
        </section>

        {/* ── Features ── */}
        <section className="max-w-4xl mx-auto px-6 pb-16 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {features.map((f) => (
            <div key={f.title} className="bg-white border border-gray-100 rounded-xl p-5">
              <div className={`w-9 h-9 ${f.bg} rounded-lg flex items-center justify-center mb-3`}>
                {f.icon}
              </div>
              <h3 className="text-sm font-semibold mb-1">{f.title}</h3>
              <p className="text-xs text-gray-500 leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </section>

        {/* ── CTA ── */}
        {!user && (
          <section className="max-w-xl mx-auto px-6 pb-20 text-center">
            <div className="bg-[#185FA5] rounded-2xl px-8 py-10">
              <h2 className="text-xl font-bold text-white mb-2">
                Your next research paper just got easier.
              </h2>
              <p className="text-blue-200 text-sm mb-6">
                Upload a document and ask your first question in under a minute.
              </p>
              <a href={`${AUTH_URL}/login`}
                className="inline-block px-6 py-2.5 text-sm rounded-lg bg-white text-[#185FA5] font-semibold hover:bg-blue-50 transition-colors">
                Get started free
              </a>
            </div>
          </section>
        )}
      </main>

      {/* ── Footer ── */}
      <footer className="border-t border-gray-100 px-8 py-4 flex justify-between items-center text-xs text-gray-400">
        <span>© 2026 SecureStream</span>
        <div className="flex gap-4">
          <a href="/privacy" className="hover:text-gray-600">Privacy</a>
          <a href="/terms" className="hover:text-gray-600">Terms</a>
        </div>
      </footer>
    </div>
  );
}