import { useEffect, useState } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import LandingPage from './components/landingPage';

function App() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  // 🔄 Fetch user from backend
  useEffect(() => {
    fetch('http://localhost:3000', {
      credentials: 'include'
    })
      .then(res => res.json())
      .then(data => {
        if (data.isAuthenticated) {
          setUser(data.user);
        }
      })
      .catch(err => console.error("Fetch error:", err))
      .finally(() => setLoading(false));
  }, []);

  // 🔐 Login
  const handleLogin = () => {
    window.location.href = 'http://localhost:3000/login';
  };

  // 🚪 Logout
  const handleLogout = () => {
    window.location.href = 'http://localhost:3000/logout';
  };

  // ⏳ Optional loading state
  if (loading) {
    return <h2 style={{ textAlign: 'center' }}>Loading...</h2>;
  }

  return (
    <Router>
      <Routes>
        {/* ✅ Main landing page */}
        <Route
          path="/"
          element={
            <LandingPage
              user={user}
              onLogin={handleLogin}
              onLogout={handleLogout}
            />
          }
        />

        {/* ✅ Fix for "/login" route error */}
        <Route path="/login" element={<Navigate to="/" />} />

        {/* ✅ Catch-all route (prevents crashes) */}
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </Router>
  );
}

export default App;