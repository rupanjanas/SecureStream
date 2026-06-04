import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";

const BACKEND = import.meta.env.VITE_BACKEND_URL;

export default function JoinPage({ user, authLoading }) {
  const { token }  = useParams();          // ← FIX: path param, not query param
  const navigate   = useNavigate();
  const [status, setStatus] = useState("verifying");  // verifying | joining | error
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    // Wait until App.jsx finishes its session fetch so `user` is reliable
    if (authLoading) return;

    if (!token) {
      setErrorMsg("Invalid invite link — no token found.");
      setStatus("error");
      return;
    }

    if (!BACKEND) {
      setErrorMsg("Configuration error — backend URL is not set.");
      setStatus("error");
      return;
    }

    if (!user) {
      setStatus("joining");
      window.location.href = `${BACKEND}/login?redirect=${encodeURIComponent(`/org/join/${token}`)}`;
      return;
    }

    // Logged in — hand off to the Node server which validates the token,
    // upserts org_members, saves the session, and redirects to /dashboard.
    setStatus("joining");
    // Small timeout so "Joining…" text renders before the hard redirect
    const t = setTimeout(() => {
      window.location.href = `${BACKEND}/org/join/${token}`;
    }, 300);
    return () => clearTimeout(t);

  }, [user, authLoading, token]);  // re-run only when auth state or token changes

  // ── UI ──────────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 font-sans">
      <div className="bg-white rounded-2xl shadow-xl px-8 py-10 w-full max-w-sm text-center">

        {status === "verifying" || status === "joining" ? (
          <>
            <div className="w-12 h-12 bg-emerald-50 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <svg className="animate-spin" width="22" height="22" viewBox="0 0 24 24"
                fill="none" stroke="#059669" strokeWidth="2.5" strokeLinecap="round">
                <path d="M21 12a9 9 0 11-6.219-8.56"/>
              </svg>
            </div>
            <p className="text-sm font-semibold text-gray-900 mb-1">
              {status === "verifying" ? "Verifying invite…" : "Joining workspace…"}
            </p>
            <p className="text-xs text-gray-400">
              You'll be redirected automatically.
            </p>
          </>
        ) : (
          <>
            <div className="w-12 h-12 bg-red-50 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
                stroke="#dc2626" strokeWidth="2.5" strokeLinecap="round">
                <circle cx="12" cy="12" r="10"/>
                <line x1="15" y1="9" x2="9"  y2="15"/>
                <line x1="9"  y1="9" x2="15" y2="15"/>
              </svg>
            </div>
            <p className="text-sm font-semibold text-gray-900 mb-1">Invite failed</p>
            <p className="text-xs text-gray-500 mb-5">{errorMsg}</p>
            <button
              onClick={() => navigate("/dashboard")}
              className="w-full py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-medium rounded-xl transition-colors"
            >
              Go to dashboard
            </button>
          </>
        )}

      </div>
    </div>
  );
}