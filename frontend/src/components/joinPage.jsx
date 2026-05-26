import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";

const BACKEND = import.meta.env.VITE_BACKEND_URL;

export default function JoinPage({ user }) {
  const [params]   = useSearchParams();
  const navigate   = useNavigate();
  const [status, setStatus] = useState("verifying"); // verifying | joining | error
  const [errorMsg, setErrorMsg] = useState("");

  const orgName = params.get("org")   || "";
  const token   = params.get("token") || "";
  const role    = params.get("role")  || "member";

  useEffect(() => {
    if (!orgName || !token) {
      setStatus("error");
      setErrorMsg("Invalid invite link — missing org or token.");
      return;
    }

    // If the user isn't logged in yet, send them to login and come back
    if (!user) {
      const returnTo = encodeURIComponent(window.location.href);
      navigate(`/login?returnTo=${returnTo}`);
      return;
    }

    const join = async () => {
      setStatus("joining");
      try {
        // Decode the invite payload (base64 JSON we created in InviteModal)
        const payload = JSON.parse(atob(token));

        // Optional: check payload.org matches query param to guard against tampering
        if (payload.org !== orgName) throw new Error("Token org mismatch");

        // Register the user in the org via your backend
        const res = await fetch(`${BACKEND}/org/join`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          // The user's own Cognito access token identifies them server-side
          credentials: "include",
          body: JSON.stringify({ org_name: orgName, role, invite_token: token }),
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || "Failed to join org");
        }

        // Navigate directly to the org workspace
        navigate("/dashboard", {
          replace: true,
          state:   { joinedOrg: orgName, role },
        });
      } catch (err) {
        setStatus("error");
        setErrorMsg(err.message || "Something went wrong. Please try again.");
      }
    };

    join();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 font-sans">
      <div className="bg-white rounded-2xl shadow-xl px-8 py-10 w-full max-w-sm text-center">
        {status === "verifying" || status === "joining" ? (
          <>
            <div className="w-12 h-12 bg-emerald-50 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <svg className="animate-spin" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#059669" strokeWidth="2.5" strokeLinecap="round">
                <path d="M21 12a9 9 0 11-6.219-8.56"/>
              </svg>
            </div>
            <p className="text-sm font-semibold text-gray-900 mb-1">
              {status === "verifying" ? "Verifying invite…" : `Joining ${orgName}…`}
            </p>
            <p className="text-xs text-gray-400">You'll be redirected to the workspace automatically.</p>
          </>
        ) : (
          <>
            <div className="w-12 h-12 bg-red-50 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#dc2626" strokeWidth="2.5" strokeLinecap="round">
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