import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Navbar from "../components/Navbar";
import { getHealth } from "../api/aiService";

export default function Dashboard({ user }) {
  const navigate        = useNavigate();
  const [health, setHealth] = useState(null);

  useEffect(() => {
    getHealth().then(setHealth);
  }, []);

  const aiOnline = health?.status === "ok";
  const dbOnline = health?.db === "connected";

  const stats = [
    { label: "AI service",      value: aiOnline ? "Online"  : "Offline",    color: aiOnline ? "text-emerald-600" : "text-red-500"  },
    { label: "Vector DB",       value: dbOnline ? "Connected" : "Error",    color: dbOnline ? "text-emerald-600" : "text-red-500"  },
    { label: "Org isolation",   value: "Active",   color: "text-violet-600" },
    { label: "Encryption",      value: "AES-256",  color: "text-[#185FA5]"  },
  ];

  return (
    <div className="min-h-screen flex flex-col bg-gray-50 font-sans">
      <Navbar user={user} />
      <main className="flex-1 max-w-5xl mx-auto w-full px-6 py-10">

        {/* Header */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-gray-900">
            {user?.given_name ? `Welcome back, ${user.given_name}` : "Dashboard"}
          </h1>
          <p className="text-sm text-gray-400 mt-1">
            Your secure document intelligence workspace
          </p>
        </div>

        {/* Stat cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          {stats.map((s) => (
            <div key={s.label}
              className="bg-white border border-gray-100 rounded-xl p-5">
              <p className="text-xs text-gray-400 mb-1">{s.label}</p>
              <p className={`text-xl font-semibold ${s.color}`}>{s.value}</p>
            </div>
          ))}
        </div>

        {/* Quick actions */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-8">
          <button
            onClick={() => navigate("/upload")}
            className="bg-white border border-gray-100 rounded-xl p-6 text-left hover:border-blue-200 hover:shadow-sm transition-all group"
          >
            <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center mb-3 group-hover:bg-blue-100 transition-colors">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                stroke="#185FA5" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
            </div>
            <h3 className="text-sm font-semibold text-gray-900 mb-1">Upload document</h3>
            <p className="text-xs text-gray-400 leading-relaxed">
              Add PDF or TXT files. They get chunked, embedded with nomic-embed-text, and stored in your org's vector namespace.
            </p>
          </button>

          <button
            onClick={() => navigate("/chat")}
            className="bg-white border border-gray-100 rounded-xl p-6 text-left hover:border-emerald-200 hover:shadow-sm transition-all group"
          >
            <div className="w-10 h-10 bg-emerald-50 rounded-lg flex items-center justify-center mb-3 group-hover:bg-emerald-100 transition-colors">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                stroke="#0F6E56" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
              </svg>
            </div>
            <h3 className="text-sm font-semibold text-gray-900 mb-1">Ask AI</h3>
            <p className="text-xs text-gray-400 leading-relaxed">
              Ask natural language questions. Llama 3 answers using only your org's documents — no data leaks across orgs.
            </p>
          </button>
        </div>

        {/* System status */}
        <div className="bg-white border border-gray-100 rounded-xl p-5">
          <h3 className="text-sm font-semibold text-gray-700 mb-4">System status</h3>
          <div className="flex flex-col gap-3">
            {[
              { label: "FastAPI AI service (port 8000)", ok: aiOnline  },
              { label: "Supabase pgvector DB",           ok: dbOnline  },
              { label: "Ollama LLM (llama3)",            ok: aiOnline  },
              { label: "nomic-embed-text embeddings",    ok: aiOnline  },
            ].map((s) => (
              <div key={s.label}
                className="flex items-center justify-between text-sm py-1 border-b border-gray-50 last:border-0">
                <span className="text-gray-500">{s.label}</span>
                <span className={`flex items-center gap-1.5 text-xs font-medium ${
                  s.ok ? "text-emerald-600" : "text-red-500"
                }`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${
                    s.ok ? "bg-emerald-500" : "bg-red-400"
                  }`}/>
                  {s.ok ? "Operational" : "Offline"}
                </span>
              </div>
            ))}
          </div>
        </div>
      </main>

      <footer className="border-t border-gray-100 px-8 py-4 flex justify-between items-center text-xs text-gray-400">
        <span>© 2026 SecureStream</span>
        <div className="flex gap-4">
          <a href="/privacy" className="hover:text-gray-600">Privacy</a>
          <a href="/terms" className="hover:text-gray-600">Terms</a>
        </div>
      </footer>
    </div>
  );
}