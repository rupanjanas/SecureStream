import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { generateInvite, sendEmailInvite } from "../api/orgService";

export default function OrgSetupPage() {
  const [step, setStep]           = useState("name");
  const [orgName, setOrgName]     = useState("");
  const [inviteUrl, setInviteUrl] = useState("");
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState(null);
  const [copied, setCopied]       = useState(false);
  const [emailInput, setEmailInput] = useState("");
  const [emailSent, setEmailSent] = useState(false);
  const [emailLoading, setEmailLoading] = useState(false);
  const [emailError, setEmailError] = useState(null);
  const navigate = useNavigate();

  const handleCreate = () => {
    if (!orgName.trim()) return;
    sessionStorage.setItem("pendingOrgName", orgName.trim());
    window.location.href = "http://localhost:3000/login";
  };

  // Called after login redirect completes — check sessionStorage
  // This runs if user is already logged in and lands here directly
  const handleGenerateInvite = async () => {
    setLoading(true);
    try {
      const data = await generateInvite();
      setInviteUrl(data.inviteUrl);
      setStep("invite");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(inviteUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleEmailSend = async () => {
    if (!emailInput.trim()) return;
    setEmailLoading(true);
    setEmailError(null);
    try {
      await sendEmailInvite(emailInput.trim(), inviteUrl);
      setEmailSent(true);
      setEmailInput("");
    } catch {
      setEmailError("Failed to send email. Check your email config in server.js");
    } finally {
      setEmailLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col bg-white font-sans">
      <nav className="flex items-center px-8 py-3.5 border-b border-gray-100">
        <button onClick={() => navigate("/onboarding")}
          className="flex items-center gap-2 text-base font-semibold text-gray-900">
          <div className="w-7 h-7 bg-[#185FA5] rounded-lg flex items-center justify-center">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="white">
              <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
            </svg>
          </div>
          SecureStream
        </button>
      </nav>

      <main className="flex-1 flex items-center justify-center px-6 py-16">
        <div className="w-full max-w-md">

          {step === "name" && (
            <>
              <button onClick={() => navigate("/onboarding")}
                className="text-sm text-gray-400 hover:text-gray-600 flex items-center gap-1 mb-6">
                ← Back
              </button>
              <h1 className="text-2xl font-bold text-gray-900 mb-1">
                Set up your workspace
              </h1>
              <p className="text-sm text-gray-400 mb-8">
                Give your organisation a name. You can invite teammates after signing in.
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
              />

              {error && (
                <div className="bg-red-50 border border-red-100 rounded-xl px-4 py-3 text-sm text-red-600 mb-4">
                  {error}
                </div>
              )}

              <button
                onClick={handleCreate}
                disabled={!orgName.trim() || loading}
                className="w-full py-2.5 text-sm rounded-xl bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors disabled:opacity-40"
              >
                {loading ? "Creating..." : "Continue →"}
              </button>
            </>
          )}

          {step === "invite" && (
            <>
              <div className="text-center mb-8">
                <div className="w-14 h-14 bg-emerald-50 rounded-2xl flex items-center justify-center mx-auto mb-4">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none"
                    stroke="#0F6E56" strokeWidth="2" strokeLinecap="round">
                    <path d="M22 11.08V12a10 10 0 11-5.93-9.14"/>
                    <polyline points="22 4 12 14.01 9 11.01"/>
                  </svg>
                </div>
                <h1 className="text-2xl font-bold text-gray-900 mb-1">Workspace ready</h1>
                <p className="text-sm text-gray-400">Invite your teammates using either method below</p>
              </div>

              {/* Copy link */}
              <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3">
                <p className="text-xs font-medium text-gray-600 mb-2">Share invite link</p>
                <div className="flex items-center gap-2 bg-gray-50 rounded-lg px-3 py-2 mb-2">
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
                <p className="text-xs text-gray-400">Link expires in 7 days</p>
              </div>

              {/* Email invite */}
              <div className="bg-white border border-gray-200 rounded-xl p-4 mb-6">
                <p className="text-xs font-medium text-gray-600 mb-2">Send via email</p>
                <div className="flex gap-2">
                  <input
                    type="email"
                    value={emailInput}
                    onChange={(e) => setEmailInput(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleEmailSend()}
                    placeholder="colleague@company.com"
                    className="flex-1 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-[#185FA5] transition-colors"
                  />
                  <button
                    onClick={handleEmailSend}
                    disabled={!emailInput.trim() || emailLoading}
                    className="px-4 py-2 text-xs rounded-xl bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40 transition-colors"
                  >
                    {emailLoading ? "Sending..." : "Send"}
                  </button>
                </div>
                {emailSent && (
                  <p className="text-xs text-emerald-600 mt-2 flex items-center gap-1">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                    Invite sent successfully
                  </p>
                )}
                {emailError && (
                  <p className="text-xs text-red-500 mt-2">{emailError}</p>
                )}
                <p className="text-xs text-gray-400 mt-2">
                  They'll receive a styled email with the invite link
                </p>
              </div>

              <button
                onClick={() => navigate("/dashboard")}
                className="w-full py-2.5 text-sm rounded-xl bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors"
              >
                Go to dashboard →
              </button>

              <button
                onClick={handleGenerateInvite}
                className="w-full py-2 text-xs text-gray-400 hover:text-gray-600 mt-2 transition-colors"
              >
                Generate a new link
              </button>
            </>
          )}
        </div>
      </main>
    </div>
  );
}