import { useState, useEffect, useRef, useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Document, Page, pdfjs } from "react-pdf";
import Navbar from "../components/Navbar";
import { getAnnotations, createAnnotation, toggleShareAnnotation } from "../api/orgService";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";  // 🔥 IMPORTANT (.mjs)
import { askQuestionStream } from "../api/aiService"
import { getDocumentText } from "../api/aiService";

pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

const HIGHLIGHT_COLOR   = "#FEF08A";
const ANNOTATION_COLORS = ["#FCD34D", "#86EFAC", "#93C5FD", "#F9A8D4", "#C4B5FD"];

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlightText(text, phrases) {
  if (!phrases.length) return [{ text, highlight: false }];
  const pattern = phrases.map(escapeRegex).join("|");
  try {
    const regex = new RegExp(`(${pattern})`, "gi");
    return text.split(regex).map((part) => ({
      text: part,
      highlight: phrases.some((p) => p.toLowerCase() === part.toLowerCase())
    }));
  } catch {
    return [{ text, highlight: false }];
  }
}

export default function DocViewerPage({ user }) {
  const location  = useLocation();
  const navigate  = useNavigate();
  const docName   = location.state?.docName  || "Document";
  const docText   = location.state?.docText  || "";
  const file = location.state?.fileUrl;
  console.log("FILE URL:", file);
  const isPDF = file?.includes(".pdf");
  
  const [numPages, setNumPages]               = useState(null);
  const [messages, setMessages]               = useState([]);
  const [input, setInput]                     = useState("");
  const [loading, setLoading]                 = useState(false);
  const [highlights, setHighlights]           = useState([]);
  const [annotations, setAnnotations]         = useState([]);
  const [activeAnnotation, setActiveAnnotation] = useState(null);
  const [newNote, setNewNote]                 = useState("");
  const [selectedText, setSelectedText]       = useState("");
  const [noteColor, setNoteColor]             = useState(ANNOTATION_COLORS[0]);
  const [showNotePanel, setShowNotePanel]     = useState(false);
  const [savingNote, setSavingNote]           = useState(false);
  const [sharingId, setSharingId]             = useState(null);
  const bottomRef                             = useRef(null);
  const userEmail                             = user?.email || "unknown";
  const [sourcePassages, setSourcePassages]   = useState([]);
  const fromDashboard = location.state?.fromDashboard || false;
  const [, setFetchedText] = useState("");
  const pdfFile = useMemo(() => ({
  url: file,
  withCredentials: false
}), [file]);

  useEffect(() => {
  if (!fromDashboard || !docName) return;

  // Fetch all chunks for this doc and reconstruct text
  const token = localStorage.getItem("token");
  fetch(`http://localhost:8000/documents/${encodeURIComponent(docName)}/text`, {
    headers: { Authorization: `Bearer ${token}` }
  })
    .then((r) => r.json())
    .then((data) => setFetchedText(data.text || ""))
    .catch(() => {});
}, [fromDashboard, docName]);

useEffect(() => {
  const loadDoc = async () => {
    try {
      const token = localStorage.getItem("token");

      const res = await getDocumentText(docName, token);

      setFetchedText(res.text || "");
    } catch (err) {
      console.error("Failed to load document:", err);
    }
  };

  if (docName) loadDoc();
}, [docName]);

  useEffect(() => {
    if (docName) {
      getAnnotations(docName).then(setAnnotations).catch(() => {});
    }
  }, [docName]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const handleTextSelect = () => {
    const sel = window.getSelection()?.toString().trim();
    if (sel && sel.length > 3) {
      setSelectedText(sel);
      setShowNotePanel(true);
    }
  };

  const handleSaveNote = async () => {
    if (!newNote.trim() || !selectedText) return;
    setSavingNote(true);
    try {
      const ann = await createAnnotation({
        doc_name:      docName,
        selected_text: selectedText,
        note:          newNote.trim(),
        color:         noteColor,
        is_shared:     false   // private by default
      });
      setAnnotations((a) => [...a, ann]);
      setNewNote("");
      setSelectedText("");
      setShowNotePanel(false);
    } catch (err) {
      console.error("Annotation error:", err);
    } finally {
      setSavingNote(false);
    }
  };

  const handleToggleShare = async (ann) => {
    setSharingId(ann.id);
    try {
      const updated = await toggleShareAnnotation(ann.id, !ann.is_shared);
      setAnnotations((prev) =>
        prev.map((a) => a.id === ann.id ? { ...a, is_shared: updated.is_shared } : a)
      );
      if (activeAnnotation?.id === ann.id) {
        setActiveAnnotation((a) => ({ ...a, is_shared: updated.is_shared }));
      }
    } catch (err) {
      console.error("Share error:", err);
    } finally {
      setSharingId(null);
    }
  };

  // ---------------- SEND ----------------
const send = async () => {
  const question = input.trim();
  if (!question || loading) return;

  setMessages((m) => [...m, { role: "user", content: question }]);
  setInput("");
  setLoading(true);
  setHighlights([]);
  setSourcePassages([]);

  // ✅ FIX: prevent duplicate assistant bubble
  setMessages((m) => {
    if (m.length && m[m.length - 1].streaming) return m;
    return [...m, { role: "assistant", content: "", streaming: true }];
  });

  try {
    await askQuestionStream(
      question,

      // STREAM TOKEN
      (token) => {
        setMessages((m) => {
          const updated = [...m];
          const last = updated[updated.length - 1];

          updated[updated.length - 1] = {
            ...last,
            content: last.content + token
          };

          return updated;
        });
      },

      // DONE
      (sources, passages) => {
        setMessages((m) => {
          const updated = [...m];
          updated[updated.length - 1] = {
            ...updated[updated.length - 1],
            streaming: false,
            sources
          };
          return updated;
        });

        // ✅ FIX: safe + exact match + dedupe
        const phrases = [...new Set(
          (passages || [])
            .sort((a, b) => b.similarity - a.similarity)
            .map((p) => p.passage.slice(0, 120))
        )];

        setHighlights(phrases);
        setSourcePassages(passages || []);
        setLoading(false);
      }
    );
  } catch (err) {
    setMessages((m) => {
      const updated = [...m];
      updated[updated.length - 1] = {
        ...updated[updated.length - 1],
        content: `Error: ${err.message}`,
        streaming: false
      };
      return updated;
    });
    setLoading(false);
  }
};

  const safeAnnotations = Array.isArray(annotations) ? annotations : [];
  const myAnnotations     = safeAnnotations.filter((a) => a.user_email === userEmail);
  const sharedAnnotations = safeAnnotations.filter((a) => a.is_shared && a.user_email !== userEmail);
  const parts             = highlightText(docText, highlights);

  return (
    <div className="min-h-screen flex flex-col bg-gray-50 font-sans">
      <Navbar user={user} />

      <main className="flex overflow-hidden" style={{ height: "calc(100vh - 57px)" }}>

        {/* LEFT — Document */}
        <div className="flex-1 flex flex-col overflow-hidden border-r border-gray-100 bg-white">

          {/* Toolbar */}
          <div className="flex items-center justify-between px-5 py-2.5 border-b border-gray-100 flex-shrink-0">
            <div className="flex items-center gap-3">
              <button onClick={() => navigate("/dashboard")}
                className="text-xs text-gray-400 hover:text-gray-600 transition-colors">
                ← Dashboard
              </button>
              <span className="text-gray-200">|</span>
              <span className="text-sm font-medium text-gray-700 truncate max-w-xs">{docName}</span>
              <span className={`text-xs px-2 py-0.5 rounded-full ${
                isPDF ? "bg-red-50 text-red-600" : "bg-blue-50 text-blue-600"
              }`}>
                {isPDF ? "PDF" : "TXT"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {highlights.length > 0 && !isPDF && (
                <span className="text-xs bg-yellow-100 text-yellow-700 px-2 py-1 rounded-lg">
                  {highlights.length} highlights
                </span>
                
              )}
              {isPDF && highlights.length > 0 && (
  <div className="text-xs text-yellow-600 px-4 py-2">
    Highlighting not supported in PDF view
  </div>
)}
              <span className="text-xs text-gray-400">
                {myAnnotations.length} note{myAnnotations.length !== 1 ? "s" : ""}
              </span>
            </div>
          </div>

          {/* Shared annotations bar */}
          {sharedAnnotations.length > 0 && (
            <div className="px-5 py-2 border-b border-gray-100 bg-blue-50 flex items-center gap-2 flex-shrink-0 overflow-x-auto">
              <span className="text-xs text-blue-600 font-medium flex-shrink-0">Shared by team:</span>
              {sharedAnnotations.map((ann) => (
                <button
                  key={ann.id}
                  onClick={() => setActiveAnnotation(activeAnnotation?.id === ann.id ? null : ann)}
                  style={{ borderColor: ann.color, backgroundColor: ann.color + "30" }}
                  className="text-xs border rounded-lg px-2 py-1 text-gray-700 hover:opacity-80 transition-opacity flex-shrink-0"
                >
                  {ann.user_email?.split("@")[0]} · "{ann.selected_text.slice(0, 20)}..."
                </button>
              ))}
            </div>
          )}

          {/* Document content */}
          <div
            onMouseUp={!isPDF ? handleTextSelect : undefined}
            className="flex-1 overflow-y-auto"
          >
            {/* Active annotation detail */}
            {activeAnnotation && (
              <div
                style={{ borderLeftColor: activeAnnotation.color }}
                className="border-l-4 mx-5 mt-4 bg-gray-50 rounded-r-xl px-4 py-3"
              >
                <div className="flex items-start justify-between">
                  <div>
                    <p className="text-xs text-gray-400 mb-0.5">
                      {activeAnnotation.user_email === userEmail ? "Your note" : `Note by ${activeAnnotation.user_email}`}
                      {activeAnnotation.is_shared && (
                        <span className="ml-2 text-blue-500">· shared with org</span>
                      )}
                    </p>
                    <p className="text-xs text-gray-500 italic mb-1">
                      "{activeAnnotation.selected_text.slice(0, 80)}{activeAnnotation.selected_text.length > 80 ? "..." : ""}"
                    </p>
                    <p className="text-sm text-gray-900">{activeAnnotation.note}</p>
                  </div>
                  <button onClick={() => setActiveAnnotation(null)}
                    className="text-xs text-gray-400 hover:text-gray-600 ml-4 flex-shrink-0">
                    ✕
                  </button>
                </div>
                {activeAnnotation.user_email === userEmail && (
                  <button
                    onClick={() => handleToggleShare(activeAnnotation)}
                    disabled={sharingId === activeAnnotation.id}
                    className={`mt-2 text-xs px-3 py-1 rounded-lg transition-colors ${
                      activeAnnotation.is_shared
                        ? "bg-blue-50 text-blue-600 hover:bg-blue-100"
                        : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                    }`}
                  >
                    {sharingId === activeAnnotation.id
                      ? "Updating..."
                      : activeAnnotation.is_shared
                      ? "Shared with org · click to make private"
                      : "Private · click to share with org"}
                  </button>
                )}
              </div>
            )}

            {/* PDF renderer */}
            {isPDF ? (
              <div className="flex justify-center py-4 px-4">
                <Document
                  file={pdfFile}
                 onLoadSuccess={({ numPages: n }) => setNumPages(n)}
                 onLoadError={(err) => console.error("PDF LOAD ERROR:", err)}   // ✅ ADD THIS
                >
                  {Array.from({ length: numPages || 1 }, (_, i) => (
                    <Page
                      key={i + 1}
                      pageNumber={i + 1}
                      width={Math.min(600, window.innerWidth - 60)}
                      className="mb-4 border border-gray-100 rounded-lg overflow-hidden"
                    />
                  ))}
                </Document>
              </div>
            ) : (
              /* TXT renderer with highlights */
              <div className="px-8 py-6 max-w-prose mx-auto">
                {docText ? (
                  <>
                    {/* My annotations strip */}
                    {myAnnotations.length > 0 && (
                      <div className="flex flex-wrap gap-2 mb-6 pb-4 border-b border-gray-100">
                        <span className="text-xs text-gray-400 w-full">Your notes:</span>
                        {myAnnotations.map((ann) => (
                          <button
                            key={ann.id}
                            onClick={() => setActiveAnnotation(activeAnnotation?.id === ann.id ? null : ann)}
                            style={{ borderColor: ann.color }}
                            className="text-xs border rounded-lg px-2 py-1 text-gray-600 hover:bg-gray-50 flex items-center gap-1.5"
                          >
                            <span style={{ backgroundColor: ann.color }}
                              className="w-2 h-2 rounded-full inline-block"/>
                            "{ann.selected_text.slice(0, 25)}..."
                            {ann.is_shared && (
                              <span className="text-blue-400">shared</span>
                            )}
                          </button>
                        ))}
                      </div>
                    )}

                    <p className="text-sm text-gray-800 leading-8 whitespace-pre-wrap select-text">
                      {parts.map((part, i) =>
                        part.highlight ? (
                          <mark key={i}
                            style={{ backgroundColor: HIGHLIGHT_COLOR }}
                            className="rounded px-0.5 transition-all">
                            {part.text}
                          </mark>
                        ) : (
                          <span key={i}>{part.text}</span>
                        )
                      )}
                    </p>
                  </>
                ) : (
                  <div className="flex flex-col items-center justify-center h-full pt-20 text-center">
                    <div className="w-14 h-14 bg-gray-100 rounded-2xl flex items-center justify-center mb-4">
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none"
                        stroke="#9ca3af" strokeWidth="1.5" strokeLinecap="round">
                        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                        <polyline points="14 2 14 8 20 8"/>
                      </svg>
                    </div>
                    <p className="text-sm text-gray-400 mb-3">No document loaded</p>
                    <button onClick={() => navigate("/upload")}
                      className="text-sm text-[#185FA5] hover:underline">
                      Upload a document →
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Note creation panel — slides up on text select */}
          {showNotePanel && (
            <div className="flex-shrink-0 border-t border-gray-200 bg-white px-5 py-4">
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs font-medium text-gray-700">
                  Add note for:{" "}
                  <span className="text-gray-500 italic">
                    "{selectedText.slice(0, 50)}{selectedText.length > 50 ? "..." : ""}"
                  </span>
                </p>
                <button
                  onClick={() => { setShowNotePanel(false); setSelectedText(""); }}
                  className="text-xs text-gray-400 hover:text-gray-600"
                >
                  Cancel
                </button>
              </div>

              <div className="flex items-center gap-2 mb-2">
                <span className="text-xs text-gray-400">Color:</span>
                {ANNOTATION_COLORS.map((c) => (
                  <button key={c} onClick={() => setNoteColor(c)}
                    style={{ backgroundColor: c }}
                    className={`w-5 h-5 rounded-full border-2 transition-transform ${
                      noteColor === c ? "border-gray-600 scale-110" : "border-transparent"
                    }`}
                  />
                ))}
                <span className="ml-auto text-xs text-gray-400">
                  Starts as private · share anytime
                </span>
              </div>
              {/* Source passages panel — shows after AI responds */}
{sourcePassages.length > 0 && (
  <div className="flex-shrink-0 border-t border-gray-100 px-4 py-3 bg-gray-50">
    <p className="text-xs font-medium text-gray-500 mb-2">
      Sources used ({sourcePassages.length})
    </p>
    <div className="flex flex-col gap-2 max-h-32 overflow-y-auto">
      {sourcePassages.map((p, i) => (
        <div key={i}
          className="bg-white border border-yellow-200 rounded-lg px-3 py-2 cursor-pointer hover:border-yellow-400 transition-colors"
         onClick={() => {
  if (isPDF && p.page !== undefined) {
    const page = p.page + 1;
    const el = document.querySelector(`[data-page-number="${page}"]`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  } else {
    const marks = document.querySelectorAll("mark");
    if (marks[i]) {
      marks[i].scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }
}}
        >
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs font-medium text-gray-600 truncate">
              {p.doc_name}
            </span>
            <span className="text-xs text-yellow-600 ml-2 flex-shrink-0">
              {Math.round(p.similarity * 100)}% match
            </span>
          </div>
          <p className="text-xs text-gray-500 leading-relaxed line-clamp-2">
            {p.passage.slice(0, 120)}...
          </p>
        </div>
      ))}
    </div>
  </div>
)}
              <div className="flex gap-2">
                <input
                  type="text"
                  value={newNote}
                  onChange={(e) => setNewNote(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleSaveNote()}
                  placeholder="Write your note..."
                  className="flex-1 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-[#185FA5] transition-colors"
                  autoFocus
                />
                <button
                  onClick={handleSaveNote}
                  disabled={!newNote.trim() || savingNote}
                  className="px-4 py-2 text-xs rounded-xl bg-[#185FA5] text-white hover:bg-[#0C447C] disabled:opacity-40 transition-colors"
                >
                  {savingNote ? "Saving..." : "Save note"}
                </button>
              </div>
            </div>
          )}
        </div>

        {/* RIGHT — AI Chat */}
        <div className="w-96 flex flex-col bg-white shrink-0">
          <div className="px-4 py-3 border-b border-gray-100 shrink-0">
            <p className="text-sm font-semibold text-gray-900">Ask AI</p>
            <p className="text-xs text-gray-400">
              {isPDF ? "Queries your embedded PDF" : "Highlights relevant passages"}
            </p>
          </div>

          <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-4">
            {messages.length === 0 && (
              <div className="text-center mt-10">
                <div className="w-10 h-10 bg-blue-50 rounded-xl flex items-center justify-center mx-auto mb-3">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                    stroke="#185FA5" strokeWidth="2" strokeLinecap="round">
                    <circle cx="11" cy="11" r="8"/>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"/>
                  </svg>
                </div>
                <p className="text-xs text-gray-400 leading-relaxed px-4">
                  Ask anything about this document. For TXT files, matching passages are highlighted automatically.
                </p>
                {!isPDF && (
                  <p className="text-xs text-gray-400 mt-2">
                    Select any text to add a private annotation.
                  </p>
                )}
              </div>
            )}

            {messages.map((msg, i) => (
              <div key={i} className={`flex gap-2 ${msg.role === "user" ? "flex-row-reverse" : ""}`}>
                {msg.role === "assistant" && (
                  <div className="w-6 h-6 bg-[#185FA5] rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5">
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="white">
                      <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
                    </svg>
                  </div>
                )}
                <div className={`rounded-2xl px-3 py-2.5 text-xs leading-relaxed max-w-[82%] ${
                  msg.role === "user"
                    ? "bg-[#185FA5] text-white rounded-tr-sm"
                    : "bg-gray-50 border border-gray-100 text-gray-800 rounded-tl-sm"
                }`}>
                  {msg.content}
                </div>
              </div>
            ))}

            {loading && (
              <div className="flex gap-2">
                <div className="w-6 h-6 bg-[#185FA5] rounded-lg flex items-center justify-center flex-shrink-0">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="white">
                    <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
                  </svg>
                </div>
                <div className="bg-gray-50 border border-gray-100 rounded-2xl rounded-tl-sm px-3 py-2.5 flex gap-1">
                  {[0,150,300].map((d) => (
                    <span key={d} className="w-1.5 h-1.5 bg-gray-300 rounded-full animate-bounce"
                      style={{ animationDelay: `${d}ms` }}/>
                  ))}
                </div>
              </div>
            )}
            <div ref={bottomRef}/>
          </div>

          <div className="flex-shrink-0 px-4 py-3 border-t border-gray-100">
            <div className="flex gap-2">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && send()}
                placeholder="Ask about this document..."
                className="flex-1 border border-gray-200 rounded-xl px-3 py-2 text-xs focus:outline-none focus:border-[#185FA5] transition-colors"
              />
              <button onClick={send} disabled={!input.trim() || loading}
                className="px-3 rounded-xl bg-[#185FA5] text-white hover:bg-[#0C447C] disabled:opacity-40 transition-colors">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
                  stroke="white" strokeWidth="2" strokeLinecap="round">
                  <line x1="22" y1="2" x2="11" y2="13"/>
                  <polygon points="22 2 15 22 11 13 2 9 22 2"/>
                </svg>
              </button>
            </div>
            <p className="text-xs text-gray-400 text-center mt-1.5">
              {isPDF ? "PDF rendered · AI searches embedded content" : "Select text to annotate · Enter to ask"}
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}