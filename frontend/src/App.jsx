import { useEffect, useState } from "react";
import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import LandingPage    from "./components/landingPage";
import OnboardingPage from "./components/Onboarding";
import OrgSetupPage   from "./components/OrgSetUp";
import Dashboard      from "./components/Dashboard";
import UploadPage     from "./components/UploadPage";
import ChatPage       from "./components/ChatPage";
import DocViewerPage  from "./components/DocViewer";
import WorkspaceSelectPage from "./components/WorkSpaceSelect";

function ProtectedRoute({ user, children }) {
  useEffect(() => {
    if (!user) window.location.href = "http://localhost:3000/login";
  }, [user]);
  if (!user) return null;
  return children;
}

export default function App() {
  const [user, setUser]       = useState(null);
  const [orgId, setOrgId]     = useState(null);
  const [orgName, setOrgName] = useState(null);
  const [mode, setMode]       = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("http://localhost:3000/", { credentials: "include" })
      .then((r) => r.json())
      .then((data) => {
        if (data.isAuthenticated) {
          setUser(data.user);
          setOrgId(data.orgId   || null);
          setOrgName(data.orgName || null);
          setMode(data.mode     || null);
        }
      })
      .catch((err) => console.error("Fetch error:", err))
      .finally(() => setLoading(false));
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
        <Route path="/workspace-select" element={
          <ProtectedRoute user={user}>
            <WorkspaceSelectPage />
          </ProtectedRoute>
        }/>
        <Route path="/dashboard" element={
          <ProtectedRoute user={user}>
            <Dashboard user={user} orgId={orgId} orgName={orgName} mode={mode} />
          </ProtectedRoute>
        }/>
        <Route path="/upload" element={
          <ProtectedRoute user={user}>
            <UploadPage user={user} />
          </ProtectedRoute>
        }/>
        <Route path="/chat" element={
          <ProtectedRoute user={user}>
            <ChatPage user={user} />
          </ProtectedRoute>
        }/>
        <Route path="/doc-viewer" element={
          <ProtectedRoute user={user}>
            <DocViewerPage user={user} />
          </ProtectedRoute>
        }/>
        <Route path="/login" element={<Navigate to="/"  replace />} />
        <Route path="*"      element={<Navigate to="/"  replace />} />
      </Routes>
    </Router>
  );
}