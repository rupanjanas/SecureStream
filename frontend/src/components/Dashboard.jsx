import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Navbar from "../components/Navbar";
import { getHealth, listDocuments } from "../api/aiService";

const TOKEN = "dev-token";

export default function Dashboard({ user, orgId, orgName, mode }) {
  const navigate          = useNavigate();
  const [health, setHealth]   = useState(null);
  const [docs, setDocs]       = useState([]);
  const [docsLoading, setDocsLoading] = useState(true);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth({ status: "error" }));
  }, []);

  useEffect(() => {
    listDocuments(TOKEN)
      .then((data) => setDocs(data.documents || []))
      .catch(() => setDocs([]))
      .finally(() => setDocsLoading(false));
  }, [orgId]);

  const aiOnline = health?.status === "ok";
  const dbOnline = health?.db === "connected";

  return (
    <div className="min-h-screen flex flex-col bg-gray-50 font-sans">
      <Navbar user={user} />
      <main className="flex-1 max-w-5xl mx-auto w-full px-6 py-10">

        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <h1 className="text-2xl font-bold text-gray-900">
                {user?.given_name ? `Welcome back, ${user.given_name}` : "Dashboard"}
              </h1>
              {mode && (
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                  mode === "org"
                    ? "bg-emerald-50 text-emerald-700"
                    : "bg-blue-50 text-blue-700"
                }`}>
                  {mode === "org" ? `Org: ${orgName}` : "Personal"}
                </span>
              )}
            </div>
            <p className="text-sm text-gray-400">
              {mode === "org"
                ? "Shared workspace — documents visible to all org members"
                : "Personal workspace — your private documents"}
            </p>
          </div>
          <button
            onClick={() => navigate("/workspace-select")}
            className="text-xs text-gray-400 hover:text-gray-600 border border-gray-200 px-3 py-1.5 rounded-lg hover:bg-gray-50 transition-colors"
          >
            Switch workspace
          </button>
        </div>

        {/* Status cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          {[
            { label: "Documents",    value: docs.length,               color: "text-[#185FA5]" },
            { label: "AI service",   value: aiOnline ? "Online" : "Offline",  color: aiOnline ? "text-emerald-600" : "text-red-500" },
            { label: "Vector DB",    value: dbOnline ? "Connected" : "Error", color: dbOnline ? "text-emerald-600" : "text-red-500" },
            { label: "Workspace",    value: mode === "org" ? "Shared" : "Private", color: "text-violet-600" },
          ].map((s) => (
            <div key={s.label} className="bg-white border border-gray-100 rounded-xl p-5">
              <p className="text-xs text-gray-400 mb-1">{s.label}</p>
              <p className={`text-xl font-semibold ${s.color}`}>{s.value}</p>
            </div>
          ))}
        </div>

        {/* Documents list */}
        <div className="bg-white border border-gray-100 rounded-xl mb-6">
          <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
            <h3 className="text-sm font-semibold text-gray-700">
              Your documents
            </h3>
            <button
              onClick={() => navigate("/upload")}
              className="px-3 py-1.5 text-xs rounded-lg bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors flex items-center gap-1.5"
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
                stroke="white" strokeWidth="2.5" strokeLinecap="round">
                <line x1="12" y1="5" x2="12" y2="19"/>
                <line x1="5" y1="12" x2="19" y2="12"/>
              </svg>
              Upload
            </button>
          </div>

          {docsLoading ? (
            <div className="flex justify-center py-10">
              <div className="flex gap-1.5">
                {[0,150,300].map((d) => (
                  <span key={d} className="w-1.5 h-1.5 bg-gray-300 rounded-full animate-bounce"
                    style={{ animationDelay: `${d}ms` }}/>
                ))}
              </div>
            </div>
          ) : docs.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <div className="w-12 h-12 bg-gray-100 rounded-xl flex items-center justify-center mb-3">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                  stroke="#9ca3af" strokeWidth="1.5" strokeLinecap="round">
                  <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                </svg>
              </div>
              <p className="text-sm text-gray-400 mb-3">No documents yet</p>
              <button onClick={() => navigate("/upload")}
                className="text-sm text-[#185FA5] hover:underline">
                Upload your first document →
              </button>
            </div>
          ) : (
            <div className="divide-y divide-gray-50">
              {docs.map((doc) => (
                <button
                  key={doc.doc_name}
                  onClick={() => navigate("/doc-viewer", {
                    state: {
                      docName: doc.doc_name,
                      fileUrl: doc.file_url
                    }
                  })}
                  className="w-full flex items-center gap-4 px-5 py-3.5 hover:bg-gray-50 transition-colors text-left group"
                >
                  <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${
                    doc.doc_name.endsWith(".pdf") ? "bg-red-50" : "bg-blue-50"
                  }`}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                      stroke={doc.doc_name.endsWith(".pdf") ? "#ef4444" : "#185FA5"}
                      strokeWidth="2" strokeLinecap="round">
                      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                      <polyline points="14 2 14 8 20 8"/>
                    </svg>
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-800 truncate group-hover:text-[#185FA5] transition-colors">
                      {doc.doc_name}
                    </p>
                    <p className="text-xs text-gray-400 mt-0.5">
                      {doc.chunks} chunks · {new Date(doc.created_at).toLocaleDateString("en-IN", {
                        day: "numeric", month: "short", year: "numeric"
                      })}
                    </p>
                  </div>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                    stroke="#d1d5db" strokeWidth="2" strokeLinecap="round"
                    className="flex-shrink-0 group-hover:stroke-[#185FA5] transition-colors">
                    <path d="M9 18l6-6-6-6"/>
                  </svg>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Quick actions */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <button onClick={() => navigate("/upload")}
            className="bg-white border border-gray-100 rounded-xl p-5 text-left hover:border-blue-200 hover:shadow-sm transition-all group">
            <div className="w-9 h-9 bg-blue-50 rounded-lg flex items-center justify-center mb-3 group-hover:bg-blue-100 transition-colors">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                stroke="#185FA5" strokeWidth="2" strokeLinecap="round">
                <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
            </div>
            <p className="text-sm font-semibold text-gray-900 mb-0.5">Upload document</p>
            <p className="text-xs text-gray-400">Add PDF or TXT to your knowledge base</p>
          </button>

          <button onClick={() => navigate("/chat")}
            className="bg-white border border-gray-100 rounded-xl p-5 text-left hover:border-emerald-200 hover:shadow-sm transition-all group">
            <div className="w-9 h-9 bg-emerald-50 rounded-lg flex items-center justify-center mb-3 group-hover:bg-emerald-100 transition-colors">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                stroke="#0F6E56" strokeWidth="2" strokeLinecap="round">
                <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
              </svg>
            </div>
            <p className="text-sm font-semibold text-gray-900 mb-0.5">Ask AI</p>
            <p className="text-xs text-gray-400">Query all documents with natural language</p>
          </button>
        </div>
      </main>
    </div>
  );
}