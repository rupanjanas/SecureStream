import { useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";

const BACKEND = import.meta.env.VITE_BACKEND_URL;

export default function JoinPage({ user, authLoading }) {
  const { token }  = useParams();
  const navigate   = useNavigate();

  const params = new URLSearchParams(window.location.search);
  const serverError = params.get("error");
  const messages = {
    invalid_invite: "This invite link is invalid or has already been used.",
    expired_invite: "This invite link has expired. Ask your admin to generate a new one.",
    join_failed:    "Something went wrong joining the workspace. Please try again.",
  };

  const errorMsg = !token
    ? "Invalid invite link — no token found."
    : !BACKEND
      ? "Configuration error — backend URL is not set."
      : serverError
        ? (messages[serverError] || "An unexpected error occurred.")
        : "";

  const status = authLoading ? "verifying" : errorMsg ? "error" : "joining";

  useEffect(() => {
    // Wait for App.jsx to finish its session fetch so `user` is reliable
    if (authLoading || errorMsg) return;

    const redirectUrl = !user
      ? `${BACKEND}/login?redirect=${encodeURIComponent(`/org/join/${token}`)}`
      : `${BACKEND}/org/join/${token}`;

    const t = setTimeout(() => {
      window.location.href = redirectUrl;
    }, 300);
    return () => clearTimeout(t);

  }, [user, authLoading, token, errorMsg]);

  // ── UI ───────────────────────────────────────────────────────────────────────
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