import { useState, useRef, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";

export default function Navbar({ user }) {
  const [dropOpen, setDropOpen] = useState(false);
  const dropRef  = useRef(null);
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    const handleClick = (e) => {
      if (dropRef.current && !dropRef.current.contains(e.target))
        setDropOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const initials = user
    ? (user.given_name?.[0] ?? "") + (user.family_name?.[0] ?? "")
    : "";

  const navLinks = [
    { label: "Dashboard", path: "/dashboard" },
    { label: "Upload",    path: "/upload"    },
    { label: "Ask AI",    path: "/chat"      },
  ];

  return (
    <nav className="sticky top-0 z-50 flex items-center justify-between px-8 py-3.5 border-b border-gray-100 bg-white">
      <div className="flex items-center gap-6">
        <button
          onClick={() => navigate("/")}
          className="flex items-center gap-2 text-base font-semibold text-gray-900"
        >
          <div className="w-7 h-7 bg-[#185FA5] rounded-lg flex items-center justify-center">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="white">
              <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z" />
            </svg>
          </div>
          SecureStream
        </button>

        {user && (
          <div className="flex items-center gap-1">
            {navLinks.map((l) => (
              <button
                key={l.path}
                onClick={() => navigate(l.path)}
                className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${
                  location.pathname === l.path
                    ? "bg-blue-50 text-[#185FA5] font-medium"
                    : "text-gray-500 hover:bg-gray-50"
                }`}
              >
                {l.label}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex items-center gap-2">
        {user ? (
          <div className="relative" ref={dropRef}>
            <button
              onClick={() => setDropOpen((o) => !o)}
              className="w-8 h-8 rounded-full bg-blue-100 text-blue-800 text-xs font-semibold flex items-center justify-center border border-gray-200 uppercase"
            >
              {initials || user.email?.[0]?.toUpperCase()}
            </button>
            {dropOpen && (
              <div className="absolute right-0 top-10 w-52 bg-white border border-gray-100 rounded-xl shadow-sm overflow-hidden z-50">
                <div className="px-4 py-3 border-b border-gray-100">
                  <p className="text-sm font-semibold truncate">
                    {user.given_name
                      ? `${user.given_name} ${user.family_name ?? ""}`
                      : user.email}
                  </p>
                  <span className="text-xs text-gray-500 truncate block">
                    {user.email}
                  </span>
                </div>
                <button
                  onClick={() => { navigate("/dashboard"); setDropOpen(false); }}
                  className="block w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50"
                >
                  Dashboard
                </button>
                <button
                  onClick={() => { navigate("/upload"); setDropOpen(false); }}
                  className="block w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50"
                >
                  Upload document
                </button>
                <button
                  onClick={() => { navigate("/chat"); setDropOpen(false); }}
                  className="block w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50"
                >
                  Ask AI
                </button>
                <div className="border-t border-gray-100">
                  <a
                    href="http://localhost:3000/logout"
                    className="block px-4 py-2.5 text-sm text-red-600 hover:bg-gray-50"
                  >
                    Sign out
                  </a>
                </div>
              </div>
            )}
          </div>
        ) : (
          <>
            <a href="http://localhost:3000/login"
              className="px-4 py-1.5 text-sm rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors">
              Log in
            </a>
            <a href="http://localhost:3000/login"
              className="px-4 py-1.5 text-sm rounded-lg bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors">
              Get started
            </a>
          </>
        )}
      </div>
    </nav>
  );
}