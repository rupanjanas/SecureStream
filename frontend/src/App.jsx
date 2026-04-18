import { useEffect, useState } from "react";
import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import LandingPage from "./components/landingPage";
import Dashboard  from "./components/Dashboard";
import UploadPage from "./components/UploadPage";
import ChatPage   from "./components/ChatPage";

function ProtectedRoute({ user, children }) {
  useEffect(() => {
    if (!user) {
      window.location.href = "http://localhost:3000/login";
    }
  }, [user]);

  if (!user) return null;
  return children;
}

function App() {
  const [user, setUser]       = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("http://localhost:3000", { credentials: "include" })
      .then((res) => res.json())
      .then((data) => {
        if (data.isAuthenticated) setUser(data.user);
      })
      .catch((err) => console.error("Fetch error:", err))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
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

  return (
    <Router>
      <Routes>
        <Route path="/" element={<LandingPage />} />

        <Route
          path="/dashboard"
          element={
            <ProtectedRoute user={user} loading={false}>
              <Dashboard user={user} />
            </ProtectedRoute>
          }
        />

        <Route
          path="/upload"
          element={
            <ProtectedRoute user={user} loading={false}>
              <UploadPage user={user} />
            </ProtectedRoute>
          }
        />

        <Route
          path="/chat"
          element={
            <ProtectedRoute user={user} loading={false}>
              <ChatPage user={user} />
            </ProtectedRoute>
          }
        />

        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="*"      element={<Navigate to="/" replace />} />
      </Routes>
    </Router>
  );
}

export default App;