import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";

const AUTH_URL = import.meta.env.VITE_BACKEND_URL;

export default function JoinPage({
  user,
  authLoading
}) {
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const token =
      new URLSearchParams(location.search)
        .get("token");

    if (!token) {
      navigate("/");
      return;
    }

    if (authLoading) return;

    // Not logged in
    if (!user) {
      localStorage.setItem(
        "pendingInviteToken",
        token
      );

      window.location.href =
        `${AUTH_URL}/login`;

      return;
    }

    // Logged in
    fetch(`${AUTH_URL}/org/join`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type":
          "application/json",
      },
      body: JSON.stringify({
        token,
      }),
    })
      .then(async (r) => {
  const data = await r.json();

  if (!r.ok) {
    throw new Error(data.error || "Join failed");
  }

  return data;
})
.then(() => {
  localStorage.removeItem(
    "pendingInviteToken"
  );

  navigate("/dashboard");
})
      .catch(() => {
        navigate("/");
      });
  }, [user, authLoading, location.search, navigate]);

  return (
    <div>
      Joining workspace...
    </div>
  );
}