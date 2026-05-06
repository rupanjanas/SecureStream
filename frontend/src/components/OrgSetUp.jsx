import { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { createOrg, generateInvite, sendEmailInvite } from "../api/orgService";
const AUTH_URL = import.meta.env.VITE_BACKEND_URL;
const AI_URL = import.meta.env.VITE_AI_SERVICE_URL;
export default function OrgSetupPage() {
  const [step, setStep]               = useState("name");
  const [inviteUrl, setInviteUrl]     = useState("");
  const [orgData, setOrgData]         = useState(null);
  const [loading, setLoading]         = useState(false);
  const [error, setError]             = useState(null);
  const [copied, setCopied]           = useState(false);
  const [emailInput, setEmailInput]   = useState("");
  const [emailList, setEmailList]     = useState([]);
  const [emailSending, setEmailSending] = useState(false);
  const [emailResults, setEmailResults] = useState([]);
  const [emailError, setEmailError]   = useState(null);
  const emailRef                       = useRef(null);
  const navigate                       = useNavigate();

  // Handle return from login with pending org name
  const [orgName, setOrgName] = useState(() => {
  const pending = sessionStorage.getItem("pendingOrgName");
  if (pending) {
    sessionStorage.removeItem("pendingOrgName");
    return pending;
  }
  return "";
});

  const doCreate = async (name) => {
    setLoading(true);
    setError(null);
    try {
      const data = await createOrg(name);

      if (data?.error === "not_authenticated") {
        sessionStorage.setItem("pendingOrgName", name);
        window.location.href = `${AUTH_URL}/login`;
        return;
      }

      setOrgData(data.org);

      // Generate first invite link automatically
      const invite = await generateInvite();
      setInviteUrl(invite.inviteUrl);
      setStep("invite");
    } catch (err) {
      if (err.message?.includes("401")) {
        sessionStorage.setItem("pendingOrgName", name);
        window.location.href = `${AUTH_URL}/login`;
        return;
      }
      setError(err.message || "Failed to create organisation.");
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = () => {
    const name = orgName.trim();
    if (!name) return;
    doCreate(name);
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(inviteUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleAddEmail = () => {
    const email = emailInput.trim();
    if (!email || emailList.includes(email)) return;
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setEmailError("Invalid email address");
      return;
    }
    setEmailList((l) => [...l, email]);
    setEmailInput("");
    setEmailError(null);
    emailRef.current?.focus();
  };

  const handleRemoveEmail = (email) => {
    setEmailList((l) => l.filter((e) => e !== email));
  };

  const handleSendAll = async () => {
    if (!emailList.length) return;
    setEmailSending(true);
    setEmailResults([]);
    const results = [];
    for (const email of emailList) {
      try {
        await sendEmailInvite(email, inviteUrl);
        results.push({ email, ok: true });
      } catch {
        results.push({ email, ok: false });
      }
    }
    setEmailResults(results);
    setEmailList([]);
    setEmailSending(false);
  };

  const handleRegenerateLink = async () => {
    try {
      const invite = await generateInvite();
      setInviteUrl(invite.inviteUrl);
      setCopied(false);
    } catch (err) {
      setError(err.message);
    }
  };

  const handleGoToDashboard = () => {
    navigate("/dashboard");
  };

  const handleGoToDocViewer = () => {
    navigate("/upload");
  };

  return (
    <div className="min-h-screen flex flex-col bg-white font-sans">
      <nav className="flex items-center px-8 py-3.5 border-b border-gray-100">
        <button
          onClick={() => navigate(step === "invite" ? "/workspace-select" : "/onboarding")}
          className="flex items-center gap-2 text-base font-semibold text-gray-900"
        >
          <div className="w-7 h-7 bg-[#185FA5] rounded-lg flex items-center justify-center">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="white">
              <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
            </svg>
          </div>
          SecureStream
        </button>
      </nav>

      <main className="flex-1 flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-lg">

          {/* ── STEP 1: Name ── */}
          {step === "name" && (
            <>
              <button
                onClick={() => navigate("/workspace-select")}
                className="text-sm text-gray-400 hover:text-gray-600 flex items-center gap-1 mb-6"
              >
                ← Back
              </button>
              <h1 className="text-2xl font-bold text-gray-900 mb-1">
                Set up your workspace
              </h1>
              <p className="text-sm text-gray-400 mb-8">
                Name your organisation. You'll invite teammates on the next step.
              </p>

              <label className="text-sm font-medium text-gray-600 block mb-1.5">
                Organisation name
              </label>
              <input
                type="text"
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                placeholder="e.g. Acme Legal, Research Lab..."
                className="w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-[#185FA5] transition-colors mb-4"
                autoFocus
              />

              {error && (
                <div className="bg-red-50 border border-red-100 rounded-xl px-4 py-3 text-sm text-red-600 mb-4">
                  {error}
                </div>
              )}

              <button
                onClick={handleCreate}
                disabled={!orgName.trim() || loading}
                className="w-full py-2.5 text-sm rounded-xl bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              >
                {loading ? (
                  <>
                    <svg className="animate-spin" width="14" height="14" viewBox="0 0 24 24"
                      fill="none" stroke="white" strokeWidth="2.5">
                      <circle cx="12" cy="12" r="10" strokeOpacity=".25"/>
                      <path d="M12 2a10 10 0 0110 10"/>
                    </svg>
                    Creating workspace...
                  </>
                ) : "Create workspace →"}
              </button>
            </>
          )}

          {/* ── STEP 2: Invite ── */}
          {step === "invite" && (
            <>
              {/* Workspace created banner */}
              <div className="flex items-center gap-3 bg-emerald-50 border border-emerald-100 rounded-xl px-4 py-3 mb-6">
                <div className="w-8 h-8 bg-emerald-100 rounded-lg flex items-center justify-center flex-shrink-0">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                    stroke="#0F6E56" strokeWidth="2.5" strokeLinecap="round">
                    <polyline points="20 6 9 17 4 12"/>
                  </svg>
                </div>
                <div>
                  <p className="text-sm font-semibold text-emerald-800">
                    "{orgData?.name || orgName}" workspace created
                  </p>
                  <p className="text-xs text-emerald-600">
                    You're the admin. Invite teammates to collaborate in real time.
                  </p>
                </div>
              </div>

              {/* Real-time collaboration note */}
              <div className="bg-blue-50 border border-blue-100 rounded-xl px-4 py-3 mb-6">
                <p className="text-xs font-medium text-blue-700 mb-1">
                  Real-time collaboration
                </p>
                <p className="text-xs text-blue-600 leading-relaxed">
                  Once teammates join, you'll share the same document workspace — annotations, AI queries, and uploaded documents are all visible org-wide.
                </p>
              </div>

              {/* Copy invite link */}
              <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-xs font-medium text-gray-600">Invite link</p>
                  <button
                    onClick={handleRegenerateLink}
                    className="text-xs text-gray-400 hover:text-gray-600 transition-colors"
                  >
                    Regenerate
                  </button>
                </div>
                <div className="flex items-center gap-2 bg-gray-50 rounded-lg px-3 py-2 mb-1">
                  <span className="text-xs text-gray-500 flex-1 truncate font-mono">
                    {inviteUrl}
                  </span>
                  <button
                    onClick={handleCopy}
                    className="px-3 py-1 text-xs rounded-lg bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors flex-shrink-0"
                  >
                    {copied ? "Copied!" : "Copy"}
                  </button>
                </div>
                <p className="text-xs text-gray-400">Expires in 7 days · anyone with this link joins your org</p>
              </div>

              {/* Email invites */}
              <div className="bg-white border border-gray-200 rounded-xl p-4 mb-5">
                <p className="text-xs font-medium text-gray-600 mb-2">
                  Invite by email
                </p>

                {/* Email input */}
                <div className="flex gap-2 mb-2">
                  <input
                    ref={emailRef}
                    type="email"
                    value={emailInput}
                    onChange={(e) => { setEmailInput(e.target.value); setEmailError(null); }}
                    onKeyDown={(e) => e.key === "Enter" && handleAddEmail()}
                    placeholder="teammate@company.com"
                    className="flex-1 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-[#185FA5] transition-colors"
                  />
                  <button
                    onClick={handleAddEmail}
                    disabled={!emailInput.trim()}
                    className="px-4 py-2 text-xs rounded-xl border border-gray-200 hover:bg-gray-50 text-gray-600 disabled:opacity-40 transition-colors"
                  >
                    Add
                  </button>
                </div>

                {emailError && (
                  <p className="text-xs text-red-500 mb-2">{emailError}</p>
                )}

                {/* Email chips */}
                {emailList.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mb-3">
                    {emailList.map((email) => (
                      <span key={email}
                        className="flex items-center gap-1 text-xs bg-blue-50 text-blue-700 px-2.5 py-1 rounded-full border border-blue-100">
                        {email}
                        <button
                          onClick={() => handleRemoveEmail(email)}
                          className="text-blue-400 hover:text-blue-600 ml-0.5"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                )}

                {/* Send button */}
                {emailList.length > 0 && (
                  <button
                    onClick={handleSendAll}
                    disabled={emailSending}
                    className="w-full py-2 text-xs rounded-xl bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40 transition-colors flex items-center justify-center gap-2"
                  >
                    {emailSending ? (
                      <>
                        <svg className="animate-spin" width="12" height="12" viewBox="0 0 24 24"
                          fill="none" stroke="white" strokeWidth="2.5">
                          <circle cx="12" cy="12" r="10" strokeOpacity=".25"/>
                          <path d="M12 2a10 10 0 0110 10"/>
                        </svg>
                        Sending invites...
                      </>
                    ) : `Send invite${emailList.length > 1 ? "s" : ""} to ${emailList.length} person${emailList.length > 1 ? "s" : ""}`}
                  </button>
                )}

                {/* Send results */}
                {emailResults.length > 0 && (
                  <div className="mt-2 flex flex-col gap-1">
                    {emailResults.map((r) => (
                      <p key={r.email}
                        className={`text-xs flex items-center gap-1 ${r.ok ? "text-emerald-600" : "text-red-500"}`}>
                        {r.ok ? "✓" : "✗"} {r.email} — {r.ok ? "invite sent" : "failed"}
                      </p>
                    ))}
                  </div>
                )}
              </div>

              {/* CTA buttons */}
              <div className="flex flex-col gap-2">
                <button
                  onClick={handleGoToDocViewer}
                  className="w-full py-2.5 text-sm rounded-xl bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors"
                >
                  Upload a document and start collaborating →
                </button>
                <button
                  onClick={handleGoToDashboard}
                  className="w-full py-2 text-sm text-gray-500 hover:text-gray-700 border border-gray-200 rounded-xl hover:bg-gray-50 transition-colors"
                >
                  Go to dashboard
                </button>
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}