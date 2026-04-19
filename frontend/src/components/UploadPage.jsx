import { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import Navbar from "../components/Navbar";
import { uploadDocument } from "../api/aiService";

export default function UploadPage({ user }) {
  const [dragging, setDragging] = useState(false);
  const [file, setFile]         = useState(null);
  const [status, setStatus]     = useState(null);
  const [result, setResult]     = useState(null);
  const [error, setError]       = useState(null);
  const inputRef                = useRef(null);
  const navigate                = useNavigate();
  const token                   = "dev-token";

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

    // Read text for TXT, create object URL for PDF
    let docText = "";
    let docUrl  = null;

    if (isPDF) {
    docUrl = null;   
    } else {
      docText = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload  = (e) => resolve(e.target.result);
        reader.onerror = reject;
        reader.readAsText(file);
      });
    }

    const data = await uploadDocument(file, token);
    setResult(data);
    setStatus("done");

    setTimeout(() => {
      navigate("/doc-viewer", {
      state: {
      docName: file.name,
      docText,
      file   // 🔥 PASS FILE DIRECTLY
    }
  });
    }, 1200);
  } catch (err) {
    setError(err.message || "Upload failed. Is the AI service running on port 8000?");
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
            Files are chunked, embedded with nomic-embed-text, and stored in your org's isolated namespace.
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

        {/* Error */}
        {error && (
          <div className="bg-red-50 border border-red-100 rounded-xl px-4 py-3 text-sm text-red-600 mb-4">
            {error}
          </div>
        )}

        {/* Success */}
        {status === "done" && result && (
          <div className="bg-emerald-50 border border-emerald-100 rounded-xl px-4 py-4 mb-4">
            <p className="text-sm font-semibold text-emerald-700 mb-1">
              Ingested successfully
            </p>
            <p className="text-xs text-emerald-600 mb-3">
              {result.chunks_stored} chunks stored · "{result.doc_name}"
            </p>
            <button
              onClick={() => navigate("/chat")}
              className="px-4 py-1.5 text-xs rounded-lg bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors"
            >
              Ask questions about this document →
            </button>
          </div>
        )}

        {/* Upload button */}
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
              Embedding chunks — this may take a minute...
            </>
          ) : "Upload and embed"}
        </button>

        <p className="text-xs text-gray-400 text-center mt-3">
          Documents are org-isolated. Other organizations cannot access your files.
        </p>
      </main>

      <footer className="border-t border-gray-100 px-8 py-4 flex justify-between text-xs text-gray-400">
        <span>© 2026 SecureStream</span>
        <div className="flex gap-4">
          <a href="/privacy" className="hover:text-gray-600">Privacy</a>
          <a href="/terms" className="hover:text-gray-600">Terms</a>
        </div>
      </footer>
    </div>
  );
}