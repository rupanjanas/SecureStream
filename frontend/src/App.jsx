import { useEffect, useState } from "react";
import { BrowserRouter as Router, Routes, Route, Navigate, useParams } from "react-router-dom";
import LandingPage    from "./components/landingPage";
import OnboardingPage from "./components/Onboarding";
import OrgSetupPage   from "./components/OrgSetUp";
import Dashboard      from "./components/Dashboard";
import UploadPage     from "./components/UploadPage";
import ChatPage       from "./components/ChatPage";
import DocViewerPage  from "./components/DocViewer";
import WorkspaceSelectPage from "./components/WorkSpaceSelect";
import JoinPage from "./components/joinPage";
const AUTH_URL = import.meta.env.VITE_BACKEND_URL;
const AI_URL = import.meta.env.VITE_AI_SERVICE_URL;
function ProtectedRoute({ user, children }) {
  if (!user) return <Navigate to="/" replace />;  // soft redirect, no reload
  return children;
}
function JoinOrgRedirect() {
  const { token } = useParams();

  useEffect(() => {
    // Redirect to backend which handles the join logic
    window.location.href = `${import.meta.env.VITE_BACKEND_URL}/org/join/${token}`;
  }, [token]);

  return (
    <div className="min-h-screen flex items-center justify-center">
      <p className="text-sm text-gray-500">Joining workspace...</p>
    </div>
  );
}

export default function App() {
  const [user, setUser]       = useState(null);
  const [orgId, setOrgId]     = useState(null);
  const [orgName, setOrgName] = useState(null);
  const [mode, setMode]       = useState(null);
  const [loading, setLoading] = useState(true);

  // In App.jsx — replace your session fetch with this:
useEffect(() => {
  const controller = new AbortController();
  const timeout    = setTimeout(() => controller.abort(), 8000); // 8s timeout

  fetch(`${import.meta.env.VITE_BACKEND_URL}/`, {
    credentials: "include",
    signal:      controller.signal,
  })
    .then((r) => r.json())
    .then((data) => {
      clearTimeout(timeout);
      if (data.isAuthenticated) {
        setUser(data.user);
        setMode(data.mode || "personal");
        setOrgId(data.orgId || null);
        setOrgName(data.orgName || null);
      }
      setLoading(false);
    })
    .catch((err) => {
      clearTimeout(timeout);
      if (err.name !== "AbortError") {
        console.error("Session fetch failed:", err);
      }
      setLoading(false); // ← never leave user stuck on loading
    });

  return () => {
    clearTimeout(timeout);
    controller.abort();
  };
}, []);

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
    <Router>
      <Routes>
        <Route path="/"                 element={<LandingPage />} />
        <Route path="/onboarding"       element={<OnboardingPage />} />
        <Route path="/org-setup"        element={<OrgSetupPage />} />
        <Route path="/join"             element={<JoinPage user={user} />} />
        <Route path="/org/join/:token" element={<JoinOrgRedirect />} />
        <Route path="/workspace-select" element={
  <ProtectedRoute user={user}>
    <WorkspaceSelectPage
      setMode={setMode}
      setOrgId={setOrgId}
      setOrgName={setOrgName}
    />
  </ProtectedRoute>
}/>
        <Route path="/dashboard" element={
          <ProtectedRoute user={user}>
            <Dashboard
             user={user} orgId={orgId} orgName={orgName} mode={mode}
            setMode={setMode} setOrgId={setOrgId} setOrgName={setOrgName}  // ← add these
            />
          </ProtectedRoute>
        }/>
        <Route path="/upload" element={
          <ProtectedRoute user={user}>
          <UploadPage user={user} mode={mode} orgId={orgId} />
          </ProtectedRoute>
        }/>
        <Route path="/chat" element={
          <ProtectedRoute user={user}>
            <ChatPage user={user} orgId={orgId} mode={mode} />
          </ProtectedRoute>
        }/>
        <Route path="/doc-viewer" element={
          <ProtectedRoute user={user}>
          <DocViewerPage user={user} mode={mode} orgName={orgName} />
        </ProtectedRoute>
        }/>
        <Route path="/login" element={<Navigate to="/"  replace />} />
        <Route path="*"      element={<Navigate to="/"  replace />} />
      </Routes>
    </Router>
  );
}