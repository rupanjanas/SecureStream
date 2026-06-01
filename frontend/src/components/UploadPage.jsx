import { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import Navbar from "../components/Navbar";
import { uploadDocument, getSession } from "../api/aiService";
import { storeFile } from "../utils/filestore";

export default function UploadPage({ user, mode, orgId }) {
  const [dragging, setDragging]   = useState(false);
  const [file, setFile]           = useState(null);
  const [status, setStatus]       = useState(null);
  const [result, setResult]       = useState(null);
  const [error, setError]         = useState(null);
  const inputRef                  = useRef(null);
  const navigate                  = useNavigate();
  const effectiveOrgId = mode === "org" ? orgId : null;

  const handleFile = (f) => {
    if (!f) return;
    if (!["application/pdf", "text/plain"].includes(f.type)) {
      setError("Only PDF and TXT files are supported.");
      return;
    }
    setFile(f);
    setError(null);
    setStatus(null);
    setResult(null);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    handleFile(e.dataTransfer.files[0]);
  };

  const handleUpload = async () => {
    if (!file) return;
    setStatus("uploading");
    setError(null);

    try {
      const isPDF = file.type === "application/pdf";
      let docText = "";

      if (!isPDF) {
        docText = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload  = (e) => resolve(e.target.result);
          reader.onerror = reject;
          reader.readAsText(file);
        });
      }

      // Get token
      const session = await getSession();
      const token   = session?.access_token || "dev-token";

      // Upload — pass orgId so ingest scopes correctly
      const data = await uploadDocument(file, token, effectiveOrgId);
      setResult(data);
      setStatus("done");

      setTimeout(() => {
        if (isPDF) {
          // For personal mode: store file object for blob URL in viewer
          // For org mode:      the viewer will use data.file_url from storage
          if (mode !== "org") storeFile(file);
        }

        navigate("/doc-viewer", {
          state: {
            docName:  file.name,
            docText:  isPDF ? "" : docText,
            file_url: data.file_url || null,   // ← remote URL from Supabase Storage
            fromDashboard: false,
          }
        });
      }, 1200);
    } catch (err) {
      setError(err.message || "Upload failed. Is the AI service running?");
      setStatus("error");
    }
  };

  return (
    <div className="min-h-screen flex flex-col bg-gray-50 font-sans">
      <Navbar user={user} />
      <main className="flex-1 max-w-2xl mx-auto w-full px-6 py-10">

        <div className="mb-6">
          <button onClick={() => navigate("/dashboard")}
            className="text-sm text-gray-400 hover:text-gray-600 flex items-center gap-1 mb-3">
            ← Back to dashboard
          </button>
          <h1 className="text-2xl font-bold text-gray-900">Upload document</h1>
          <p className="text-sm text-gray-400 mt-1">
            {mode === "org"
              ? "Document will be shared with your entire organisation."
              : "Document will be stored in your private workspace."}
          </p>
        </div>

        {/* Drop zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          className={`border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-all mb-4 ${
            dragging
              ? "border-[#185FA5] bg-blue-50"
              : file
              ? "border-emerald-300 bg-emerald-50"
              : "border-gray-200 bg-white hover:border-gray-300"
          }`}
        >
          <input ref={inputRef} type="file" accept=".pdf,.txt"
            className="hidden" onChange={(e) => handleFile(e.target.files[0])} />

          {file ? (
            <>
              <div className="w-12 h-12 bg-emerald-100 rounded-xl flex items-center justify-center mx-auto mb-3">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
                  stroke="#0F6E56" strokeWidth="2" strokeLinecap="round">
                  <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                </svg>
              </div>
              <p className="text-sm font-semibold text-gray-800">{file.name}</p>
              <p className="text-xs text-gray-400 mt-1">
                {(file.size / 1024).toFixed(1)} KB · click to change
              </p>
            </>
          ) : (
            <>
              <div className="w-12 h-12 bg-blue-50 rounded-xl flex items-center justify-center mx-auto mb-3">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
                  stroke="#185FA5" strokeWidth="2" strokeLinecap="round">
                  <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                  <polyline points="17 8 12 3 7 8"/>
                  <line x1="12" y1="3" x2="12" y2="15"/>
                </svg>
              </div>
              <p className="text-sm font-semibold text-gray-700">
                Drop your file here or click to browse
              </p>
              <p className="text-xs text-gray-400 mt-1">PDF or TXT · up to 10 MB</p>
            </>
          )}
        </div>

        {error && (
          <div className="bg-red-50 border border-red-100 rounded-xl px-4 py-3 text-sm text-red-600 mb-4">
            {error}
          </div>
        )}

        {status === "done" && result && (
          <div className="bg-emerald-50 border border-emerald-100 rounded-xl px-4 py-4 mb-4">
            <p className="text-sm font-semibold text-emerald-700 mb-1">Ingested successfully</p>
            <p className="text-xs text-emerald-600">
              {result.chunks_stored} chunks stored · "{result.doc_name}"
              {mode === "org" && " · shared with org"}
            </p>
          </div>
        )}

        <button
          onClick={handleUpload}
          disabled={!file || status === "uploading"}
          className="w-full py-2.5 text-sm rounded-xl bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2"
        >
          {status === "uploading" ? (
            <>
              <svg className="animate-spin" width="15" height="15" viewBox="0 0 24 24"
                fill="none" stroke="white" strokeWidth="2.5">
                <circle cx="12" cy="12" r="10" strokeOpacity=".25"/>
                <path d="M12 2a10 10 0 0110 10"/>
              </svg>
              Uploading and embedding...
            </>
          ) : "Upload and embed"}
        </button>

        <p className="text-xs text-gray-400 text-center mt-3">
          {mode === "org"
            ? "Shared with your organisation. Members can view and query this document."
            : "Private to your account. Only you can access this document."}
        </p>
      </main>
    </div>
  );
}