import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { getMemberships, selectWorkspace } from "../api/orgService";

export default function WorkspaceSelectPage() {
  const [memberships, setMemberships] = useState([]);
  const [loading, setLoading]         = useState(true);
  const [selecting, setSelecting]     = useState(null);
  const [inviteInput, setInviteInput] = useState("");
  const [showInvite, setShowInvite]   = useState(false);
  const navigate                      = useNavigate();

  useEffect(() => {
    getMemberships()
      .then((data) => setMemberships(data.memberships || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleSelect = async (mode, orgId = null) => {
    setSelecting(orgId || mode);
    try {
      await selectWorkspace(mode, orgId);
      navigate("/dashboard");
    } catch (err) {
      console.error(err);
    } finally {
      setSelecting(null);
    }
  };

  const handleJoinInvite = () => {
    if (!inviteInput.trim()) return;
    // Extract token from URL if full URL pasted
    const token = inviteInput.includes("/org/join/")
      ? inviteInput.split("/org/join/")[1]
      : inviteInput.trim();
    window.location.href = `http://localhost:3000/org/join/${token}`;
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="flex gap-1.5">
          {[0,150,300].map((d) => (
            <span key={d} className="w-2 h-2 bg-gray-300 rounded-full animate-bounce"
              style={{ animationDelay: `${d}ms` }}/>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col bg-white font-sans">
      <nav className="flex items-center px-8 py-3.5 border-b border-gray-100">
        <div className="flex items-center gap-2 text-base font-semibold">
          <div className="w-7 h-7 bg-[#185FA5] rounded-lg flex items-center justify-center">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="white">
              <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
            </svg>
          </div>
          SecureStream
        </div>
      </nav>

      <main className="flex-1 flex flex-col items-center justify-center px-6 py-12">
        <h1 className="text-2xl font-bold text-gray-900 mb-1 text-center">
          Choose your workspace
        </h1>
        <p className="text-sm text-gray-400 mb-8 text-center">
          Your documents and history are saved per workspace
        </p>

        <div className="w-full max-w-lg flex flex-col gap-3">

          {/* Personal workspace */}
          <button
            onClick={() => handleSelect("personal")}
            disabled={selecting === "personal"}
            className="bg-white border border-gray-200 rounded-xl p-5 text-left hover:border-[#185FA5] hover:shadow-sm transition-all group flex items-center gap-4"
          >
            <div className="w-10 h-10 bg-blue-50 rounded-xl flex items-center justify-center flex-shrink-0 group-hover:bg-blue-100 transition-colors">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                stroke="#185FA5" strokeWidth="2" strokeLinecap="round">
                <circle cx="12" cy="8" r="4"/>
                <path d="M4 20c0-4 3.58-7 8-7s8 3 8 7"/>
              </svg>
            </div>
            <div className="flex-1">
              <p className="text-sm font-semibold text-gray-900">Personal workspace</p>
              <p className="text-xs text-gray-400 mt-0.5">
                Your private documents — not shared with anyone
              </p>
            </div>
            {selecting === "personal" ? (
              <svg className="animate-spin w-4 h-4 text-gray-400" viewBox="0 0 24 24"
                fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" strokeOpacity=".25"/>
                <path d="M12 2a10 10 0 0110 10"/>
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                stroke="#9ca3af" strokeWidth="2" strokeLinecap="round">
                <path d="M9 18l6-6-6-6"/>
              </svg>
            )}
          </button>

          {/* Existing org memberships */}
          {memberships.length > 0 && (
            <>
              <p className="text-xs text-gray-400 px-1 mt-1">Your organisations</p>
              {memberships.map((m) => (
                <button
                  key={m.org_id}
                  onClick={() => handleSelect("org", m.org_id)}
                  disabled={!!selecting}
                  className="bg-white border border-gray-200 rounded-xl p-5 text-left hover:border-emerald-400 hover:shadow-sm transition-all group flex items-center gap-4"
                >
                  <div className="w-10 h-10 bg-emerald-50 rounded-xl flex items-center justify-center flex-shrink-0 group-hover:bg-emerald-100 transition-colors">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                      stroke="#0F6E56" strokeWidth="2" strokeLinecap="round">
                      <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/>
                      <circle cx="9" cy="7" r="4"/>
                      <path d="M23 21v-2a4 4 0 00-3-3.87"/>
                      <path d="M16 3.13a4 4 0 010 7.75"/>
                    </svg>
                  </div>
                  <div className="flex-1">
                    <p className="text-sm font-semibold text-gray-900">
                      {m.orgs?.name || "Organisation"}
                    </p>
                    <p className="text-xs text-gray-400 mt-0.5 capitalize">
                      {m.role} · click to open
                    </p>
                  </div>
                  {selecting === m.org_id ? (
                    <svg className="animate-spin w-4 h-4 text-gray-400" viewBox="0 0 24 24"
                      fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="10" strokeOpacity=".25"/>
                      <path d="M12 2a10 10 0 0110 10"/>
                    </svg>
                  ) : (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                      stroke="#9ca3af" strokeWidth="2" strokeLinecap="round">
                      <path d="M9 18l6-6-6-6"/>
                    </svg>
                  )}
                </button>
              ))}
            </>
          )}

          {/* Create new org */}
          <button
            onClick={() => navigate("/org-setup")}
            className="bg-white border border-dashed border-gray-200 rounded-xl p-5 text-left hover:border-gray-300 transition-all flex items-center gap-4"
          >
            <div className="w-10 h-10 bg-gray-50 rounded-xl flex items-center justify-center flex-shrink-0">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                stroke="#9ca3af" strokeWidth="2" strokeLinecap="round">
                <line x1="12" y1="5" x2="12" y2="19"/>
                <line x1="5" y1="12" x2="19" y2="12"/>
              </svg>
            </div>
            <div>
              <p className="text-sm font-semibold text-gray-600">Create new organisation</p>
              <p className="text-xs text-gray-400 mt-0.5">Set up a shared workspace for your team</p>
            </div>
          </button>

          {/* Join via invite */}
          {!showInvite ? (
            <button
              onClick={() => setShowInvite(true)}
              className="text-sm text-[#185FA5] hover:underline text-center py-2"
            >
              Have an invite link? Join an organisation →
            </button>
          ) : (
            <div className="bg-blue-50 border border-blue-100 rounded-xl p-4">
              <p className="text-xs font-medium text-blue-700 mb-2">
                Paste your invite link or token
              </p>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={inviteInput}
                  onChange={(e) => setInviteInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleJoinInvite()}
                  placeholder="http://localhost:3000/org/join/... or token"
                  className="flex-1 border border-blue-200 rounded-xl px-3 py-2 text-xs focus:outline-none focus:border-[#185FA5] bg-white"
                />
                <button
                  onClick={handleJoinInvite}
                  disabled={!inviteInput.trim()}
                  className="px-4 py-2 text-xs rounded-xl bg-[#185FA5] text-white hover:bg-[#0C447C] disabled:opacity-40 transition-colors"
                >
                  Join
                </button>
              </div>
              <button onClick={() => setShowInvite(false)}
                className="text-xs text-gray-400 hover:text-gray-600 mt-2">
                Cancel
              </button>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}