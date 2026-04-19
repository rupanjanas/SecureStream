import { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";

const features = [
  {
    title: "OIDC / OAuth 2.0",
    desc: "Full OpenID Connect with nonce, state, and PKCE validation via AWS Cognito.",
    bg: "bg-blue-50",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="#185FA5">
        <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z" />
      </svg>
    ),
  },
  {
    title: "Secure sessions",
    desc: "HttpOnly cookies with auto-expiry and server-side destroy on logout.",
    bg: "bg-emerald-50",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
        <rect x="3" y="11" width="18" height="11" rx="2" fill="#0F6E56" />
        <path d="M7 11V7a5 5 0 0110 0v4" stroke="#0F6E56" strokeWidth="2" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    title: "User profile",
    desc: "Claims fetched from Cognito and surfaced across your app via session.",
    bg: "bg-violet-50",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="#534AB7">
        <circle cx="12" cy="8" r="4" />
        <path d="M4 20c0-4 3.58-7 8-7s8 3 8 7" />
      </svg>
    ),
  },
  {
    title: "Env-driven config",
    desc: "All secrets live in .env — no hardcoded credentials anywhere.",
    bg: "bg-green-50",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
        <path d="M9 12l2 2 4-4" stroke="#3B6D11" strokeWidth="2.5" strokeLinecap="round" />
        <circle cx="12" cy="12" r="9" stroke="#3B6D11" strokeWidth="1.5" />
      </svg>
    ),
  },
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

    fetch("http://localhost:3000", { credentials: "include" })
      .then((r) => r.json())
      .then((data) => {
        if (data.isAuthenticated) setUser(data.user);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    function handleClick(e) {
      if (dropRef.current && !dropRef.current.contains(e.target)) {
        setDropOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const initials = user
    ? (user.given_name?.[0] ?? "") + (user.family_name?.[0] ?? "")
    : "";

  return (
    <div className="min-h-screen flex flex-col bg-white text-gray-900 font-sans">
      <nav className="sticky top-0 z-50 flex items-center justify-between px-8 py-3.5 border-b border-gray-100 bg-white">
        <div className="flex items-center gap-2 text-base font-semibold">
          <div className="w-7 h-7 bg-[#185FA5] rounded-lg flex items-center justify-center">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="white">
              <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z" />
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
                      {user.given_name
                        ? `${user.given_name} ${user.family_name ?? ""}`
                        : user.email}
                    </p>
                    <span className="text-xs text-gray-500 truncate block">
                      {user.email}
                    </span>
                  </div>
                  <button
                    onClick={() => { setDropOpen(false); navigate("/dashboard"); }}
                    className="block w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50"
                  >
                    Dashboard
                  </button>
                  <button
                    onClick={() => { setDropOpen(false); navigate("/upload"); }}
                    className="block w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50"
                  >
                    Upload document
                  </button>
                  <button
                    onClick={() => { setDropOpen(false); navigate("/chat"); }}
                    className="block w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50"
                  >
                    Ask AI
                  </button>
                  <div className="border-t border-gray-100">
                    <a
                      href="http://localhost:3000/logout"
                      className="block px-4 py-2.5 text-sm text-red-600 hover:bg-gray-50"
                    >
                      Sign out
                    </a>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <>
              <a
                href="http://localhost:3000/login"
                className="px-4 py-1.5 text-sm rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors"
              >
                Log in
              </a>
              <a
                href="http://localhost:3000/login"
                className="px-4 py-1.5 text-sm rounded-lg bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors"
              >
                Get started
              </a>
            </>
          )}
        </div>
      </nav>

      <main className="flex-1">
        <section className="max-w-xl mx-auto text-center px-6 pt-20 pb-14">
          <h1 className="text-4xl font-bold leading-tight mb-3">
            Secure auth, ready in minutes
          </h1>
          <p className="text-gray-500 text-base mb-8">
            OIDC authentication backed by AWS Cognito. Sign in and start building.
          </p>
          <div className="flex gap-3 justify-center">
              {user ? (
                <button onClick={() => navigate("/dashboard")}
                className="px-6 py-2.5 text-sm rounded-lg bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors">
                Go to dashboard →
                </button>
              ) : (
              <button onClick={() => navigate("/onboarding")}
              className="px-6 py-2.5 text-sm rounded-lg bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors">
              Get started free
              </button>
              )}
            <a
              href="http://localhost:3000/login"
              className="px-6 py-2.5 text-sm rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors"
            >
              View docs
            </a>
          </div>
        </section>

        <section className="max-w-4xl mx-auto px-6 pb-16 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {features.map((f) => (
            <div
              key={f.title}
              className="bg-white border border-gray-100 rounded-xl p-5"
            >
              <div
                className={`w-9 h-9 ${f.bg} rounded-lg flex items-center justify-center mb-3`}
              >
                {f.icon}
              </div>
              <h3 className="text-sm font-semibold mb-1">{f.title}</h3>
              <p className="text-xs text-gray-500 leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </section>
      </main>

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