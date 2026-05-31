import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";

const BACKEND = import.meta.env.VITE_BACKEND_URL;

function getStored() {
  try { return JSON.parse(sessionStorage.getItem("pendingInvite") || "{}"); }
  catch { return {}; }
}

export default function JoinPage({ user, authLoading }) {
  const [params] = useSearchParams();
  const navigate = useNavigate();

  // Resolve invite params — URL takes priority over sessionStorage fallback
  const stored  = getStored();
  const orgName = params.get("org")   || stored.orgName || "";
  const token   = params.get("token") || stored.token   || "";
  const role    = params.get("role")  || stored.role    || "member";

  const hasValidInvite = Boolean(orgName && token && BACKEND);

  // Derive initial state synchronously so first render is already correct
  const [status, setStatus] = useState(() =>
    hasValidInvite ? "verifying" : "error"
  );
  const [errorMsg, setErrorMsg] = useState(() =>
    !orgName || !token  ? "Invalid invite link — missing org or token." :
    !BACKEND            ? "Configuration error — please contact support." :
                          ""
  );

  useEffect(() => {
    // Wait until App.jsx finishes its session fetch
    if (authLoading) return;

    // Nothing valid to process
    if (!hasValidInvite) return;

    // Not logged in yet — save params and send to login
    if (!user) {
      sessionStorage.setItem(
        "pendingInvite",
        JSON.stringify({ orgName, token, role })
      );
      navigate(
        `/login?returnTo=${encodeURIComponent(window.location.href)}`,
        { replace: true }
      );
      return;
    }

    // Logged in and invite is valid — hand off to server
    sessionStorage.removeItem("pendingInvite");
    setStatus("joining");

    // Small timeout so "Joining…" text is visible before hard redirect
    const t = setTimeout(() => {
      window.location.href = `${BACKEND}/org/join/${token}`;
    }, 300);

    return () => clearTimeout(t);

  }, [user, authLoading]); // ← only re-run when auth state changes, not on every render

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 font-sans">
      <div className="bg-white rounded-2xl shadow-xl px-8 py-10 w-full max-w-sm text-center">

        {status === "verifying" || status === "joining" ? (
          <>
            <div className="w-12 h-12 bg-emerald-50 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <svg
                className="animate-spin"
                width="22"
                height="22"
                viewBox="0 0 24 24"
                fill="none"
                stroke="#059669"
                strokeWidth="2.5"
                strokeLinecap="round"
              >
                <path d="M21 12a9 9 0 11-6.219-8.56"/>
              </svg>
            </div>
            <p className="text-sm font-semibold text-gray-900 mb-1">
              {status === "verifying" ? "Verifying invite…" : `Joining ${orgName}…`}
            </p>
            <p className="text-xs text-gray-400">
              You'll be redirected to the workspace automatically.
            </p>
          </>
        ) : (
          <>
            <div className="w-12 h-12 bg-red-50 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <svg
                width="22"
                height="22"
                viewBox="0 0 24 24"
                fill="none"
                stroke="#dc2626"
                strokeWidth="2.5"
                strokeLinecap="round"
              >
                <circle cx="12" cy="12" r="10"/>
                <line x1="15" y1="9" x2="9" y2="15"/>
                <line x1="9" y1="9" x2="15" y2="15"/>
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