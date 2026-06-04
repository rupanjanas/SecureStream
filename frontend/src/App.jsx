import { useEffect, useState } from "react";
import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import LandingPage         from "./components/landingPage";
import OnboardingPage      from "./components/Onboarding";
import OrgSetupPage        from "./components/OrgSetUp";
import Dashboard           from "./components/Dashboard";
import UploadPage          from "./components/UploadPage";
import ChatPage            from "./components/ChatPage";
import DocViewerPage       from "./components/DocViewer";
import WorkspaceSelectPage from "./components/WorkSpaceSelect";
import JoinPage            from "./components/joinPage";

const AUTH_URL = import.meta.env.VITE_BACKEND_URL;

// ── ProtectedRoute ────────────────────────────────────────────────────────────
function ProtectedRoute({ user, loading, children }) {
  if (loading) return null;                      // wait — don't redirect prematurely
  if (!user)   return <Navigate to="/" replace />;
  return children;
}


// ── Loading spinner (inside Router so hooks always have context) ──────────────
function LoadingScreen() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="flex gap-1.5">
        {[0, 150, 300].map((d) => (
          <span
            key={d}
            className="w-2 h-2 bg-gray-300 rounded-full animate-bounce"
            style={{ animationDelay: `${d}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

// ── isTokenExpired helper ────────────────────────────────────────────────────
function isTokenExpired(token) {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return payload.exp * 1000 < Date.now() + 30_000;
  } catch {
    return false;
  }
}

// ── App ───────────────────────────────────────────────────────────────────────
export default function App() {
  const [user,    setUser]    = useState(null);
  const [orgId,   setOrgId]   = useState(null);
  const [orgName, setOrgName] = useState(null);
  const [mode,    setMode]    = useState(null);
  const [loading, setLoading] = useState(true);  // authLoading
  const [accessToken, setAccessToken] = useState(null); 

  // App.jsx — update the session fetch block

useEffect(() => {
  const controller = new AbortController();
  const timeout    = setTimeout(() => controller.abort(), 8000);

  const initSession = async () => {
    try {
      const res  = await fetch(`${AUTH_URL}/`, {
        credentials: "include",
        signal:      controller.signal,
      });
      const data = await res.json();

      if (!data.isAuthenticated) {
        setLoading(false);
        return;
      }

      // Check if token is already expired and refresh proactively
      let accessToken = data.access_token;
      if (accessToken && isTokenExpired(accessToken)) {
        const refreshRes = await fetch(`${AUTH_URL}/refresh`, {
          method:      "POST",
          credentials: "include",
        });
        if (refreshRes.ok) {
          const refreshed = await refreshRes.json();
          accessToken = refreshed.access_token;
        } else {
          // Can't refresh — user needs to log in again
          setLoading(false);
          return;
        }
      }

      setUser(data.user);
setMode(data.mode || "personal");
setOrgId(data.orgId || null);
setOrgName(data.orgName || null);
setAccessToken(accessToken);

const pendingInvite =
  localStorage.getItem("pendingInviteToken");

if (pendingInvite) {
  window.location.href =
    `/join?token=${pendingInvite}`;
}
    } catch (err) {
      if (err.name !== "AbortError") console.error("Session fetch failed:", err);
    } finally {
      clearTimeout(timeout);
      setLoading(false);
    }
  };

  initSession();
  return () => { clearTimeout(timeout); controller.abort(); };
}, []);
  // ── Router wraps EVERYTHING including the loading screen ──────────────────
  // This guarantees useLocation/useNavigate always have a valid context,
  // regardless of whether the session fetch has completed yet.
  return (
    <Router>
      {loading ? (
        <LoadingScreen />
      ) : (
        <Routes>
          <Route path="/"           element={<LandingPage />} />
          <Route path="/onboarding" element={<OnboardingPage />} />
          <Route path="/org-setup"  element={<OrgSetupPage />} />
          <Route path="/join" element={<JoinPage user={user} authLoading={loading}/>}/>
          {/* Protected routes */}
          <Route path="/workspace-select" element={
            <ProtectedRoute user={user} loading={loading}>
              <WorkspaceSelectPage
                setMode={setMode}
                setOrgId={setOrgId}
                setOrgName={setOrgName}
              />
            </ProtectedRoute>
          }/>
          <Route path="/dashboard" element={
            <ProtectedRoute user={user} loading={loading}>
              <Dashboard
                user={user}
                orgId={orgId}
                orgName={orgName}
                mode={mode}
                accessToken={accessToken} 
                setMode={setMode}
                setOrgId={setOrgId}
                setOrgName={setOrgName}
              />
            </ProtectedRoute>
          }/>
          <Route path="/upload" element={
            <ProtectedRoute user={user} loading={loading}>
              <UploadPage user={user} mode={mode} orgId={orgId} accessToken={accessToken} />
            </ProtectedRoute>
          }/>
          <Route path="/chat" element={
            <ProtectedRoute user={user} loading={loading}>
              <ChatPage user={user} orgId={orgId} mode={mode} />
            </ProtectedRoute>
          }/>
          <Route path="/doc-viewer" element={
            <ProtectedRoute user={user} loading={loading}>
              <DocViewerPage user={user} mode={mode} orgName={orgName} accessToken={accessToken}  />
            </ProtectedRoute>
          }/>

          {/* Fallbacks */}
          <Route path="/login" element={<Navigate to="/" replace />} />
          <Route path="*"      element={<Navigate to="/" replace />} />
        </Routes>
      )}
    </Router>
  );
}