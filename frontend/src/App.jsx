import { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";

import LandingPage    from "./components/LandingPage";
import OnboardingPage from "./components/OnboardingPage";
import Dashboard      from "./components/Dashboard";
import UploadPage     from "./components/UploadPage";
import ChatPage       from "./components/ChatPage";
import DocViewerPage  from "./components/DocViewerPage";

const AUTH_URL = import.meta.env.VITE_BACKEND_URL;

function RequireAuth({ user, authLoading, children }) {
  useEffect(() => {
    if (!authLoading && !user) {
      window.location.href = `${AUTH_URL}/login`;
    }
  }, [user, authLoading]);

  if (authLoading) {
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

  // While the redirect is in flight, render nothing
  if (!user) return null;

  return children;
}

// ── App ───────────────────────────────────────────────────────────────────────
export default function App() {
  const [user,        setUser]        = useState(null);
  const [accessToken, setAccessToken] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);

  useEffect(() => {
    fetch(`${AUTH_URL}/`, { credentials: "include" })
      .then((r) => r.json())
      .then((data) => {
        if (data.isAuthenticated) {
          setUser(data.user);
          setAccessToken(data.access_token || null);
        }
      })
      .catch(() => {})
      .finally(() => setAuthLoading(false));
  }, []);

  const authedProps = { user, accessToken };

  return (
    <BrowserRouter>
      <Routes>
        {/* Public */}
        <Route path="/"           element={<LandingPage />} />
        <Route path="/onboarding" element={<OnboardingPage />} />

        {/* Protected */}
        <Route path="/dashboard" element={
          <RequireAuth user={user} authLoading={authLoading}>
            <Dashboard {...authedProps} />
          </RequireAuth>
        }/>
        <Route path="/upload" element={
          <RequireAuth user={user} authLoading={authLoading}>
            <UploadPage {...authedProps} />
          </RequireAuth>
        }/>
        <Route path="/chat" element={
          <RequireAuth user={user} authLoading={authLoading}>
            <ChatPage {...authedProps} />
          </RequireAuth>
        }/>
        <Route path="/doc-viewer" element={
          <RequireAuth user={user} authLoading={authLoading}>
            <DocViewerPage {...authedProps} />
          </RequireAuth>
        }/>

        {/* Fallback */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}