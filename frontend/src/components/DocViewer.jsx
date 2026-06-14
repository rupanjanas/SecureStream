import { useState, useEffect, useRef, useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Document, Page, pdfjs } from "react-pdf";
import Navbar from "../components/Navbar";
import { retrieveFile } from "../utils/filestore";
import "react-pdf/dist/Page/TextLayer.css";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  askQuestionStream,
  getDocumentText,
  getChatHistory,
  saveChatHistory,
} from "../api/aiService";
pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

const AUTH_URL          = import.meta.env.VITE_BACKEND_URL;
const HIGHLIGHT_COLOR   = "#FEF08A";
const ANNOTATION_COLORS = ["#FCD34D", "#86EFAC", "#93C5FD", "#F9A8D4", "#C4B5FD"];

function fmtTime(ts) {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

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
      highlight: phrases.some((p) => p.toLowerCase() === part.toLowerCase()),
    }));
  } catch {
    return [{ text, highlight: false }];
  }
}

// ── Sources history side panel ────────────────────────────────────────────────

function SourcesPanel({ sourcesHistory, isPDF, pageRefs, onClose }) {
  const groups        = [...sourcesHistory].reverse();
  const totalPassages = sourcesHistory.reduce((n, g) => n + (g.passages?.length || 0), 0);
  const [collapsed, setCollapsed] = useState({});
  const toggle = (idx) => setCollapsed((s) => ({ ...s, [idx]: !s[idx] }));

  return (
    <div className="flex flex-col bg-white border-l border-gray-200 z-40 shadow-xl flex-shrink-0"
      style={{ width: "320px", height: "100%" }}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 bg-amber-50 flex-shrink-0">
        <div>
          <p className="text-sm font-semibold text-gray-900">Source history</p>
          <p className="text-xs text-gray-500">
            {groups.length} question{groups.length !== 1 ? "s" : ""} · {totalPassages} passage{totalPassages !== 1 ? "s" : ""}
          </p>
        </div>
        <button onClick={onClose}
          className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {groups.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full pb-10 text-center px-6">
            <div className="w-11 h-11 bg-amber-50 rounded-2xl flex items-center justify-center mb-3">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#b45309" strokeWidth="1.8" strokeLinecap="round">
                <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="9" y1="15" x2="15" y2="15"/>
                <line x1="9" y1="11" x2="15" y2="11"/>
              </svg>
            </div>
            <p className="text-xs font-medium text-gray-600 mb-1">No sources yet</p>
            <p className="text-xs text-gray-400 leading-relaxed">
              Ask a question in the chat. Matched passages will appear here.
            </p>
          </div>
        ) : (
          <div className="py-2">
            {groups.map((group, gIdx) => {
              const isCollapsed = collapsed[gIdx];
              const passages    = group.passages || [];
              return (
                <div key={group.id || gIdx} className="mb-0.5">
                  <button onClick={() => toggle(gIdx)}
                    className="w-full flex items-start gap-2.5 px-4 py-2.5 hover:bg-gray-50 transition-colors text-left">
                    <div className="w-4 h-4 rounded bg-amber-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                      <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="#b45309" strokeWidth="3" strokeLinecap="round"
                        className={`transition-transform duration-200 ${isCollapsed ? "-rotate-90" : ""}`}>
                        <polyline points="6 9 12 15 18 9"/>
                      </svg>
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium text-gray-800 leading-snug line-clamp-2">{group.question}</p>
                      <p className="text-xs text-gray-400 mt-0.5">
                        {fmtTime(group.timestamp)} · {passages.length} source{passages.length !== 1 ? "s" : ""}
                      </p>
                    </div>
                  </button>
                  {!isCollapsed && (
                    <div className="px-3 pb-2 flex flex-col gap-1.5">
                      {passages.length === 0 ? (
                        <p className="text-xs text-gray-400 px-2 py-1.5 italic">No passages found.</p>
                      ) : (
                        passages.map((p, pIdx) => (
                          <div key={pIdx}
                            onClick={() => {
                              if (!isPDF) {
                                const marks = document.querySelectorAll("mark");
                                if (marks[pIdx]) marks[pIdx].scrollIntoView({ behavior: "smooth", block: "center" });
                              } else {
                                const pageEl = pageRefs.current[p.page_number || 1];
                                if (pageEl) pageEl.scrollIntoView({ behavior: "smooth", block: "start" });
                              }
                            }}
                            className="ml-6 bg-white border border-amber-100 hover:border-amber-300 hover:bg-amber-50 rounded-lg px-3 py-2.5 cursor-pointer transition-all group">
                            <div className="flex items-center justify-between mb-1">
                              <span className="text-xs text-gray-500 truncate flex items-center gap-1 min-w-0">
                                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="flex-shrink-0">
                                  <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                                  <polyline points="14 2 14 8 20 8"/>
                                </svg>
                                <span className="truncate">{p.doc_name}</span>
                              </span>
                              <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium flex-shrink-0 ml-2 ${
                                p.similarity > 0.7 ? "bg-green-50 text-green-700"
                                : p.similarity > 0.4 ? "bg-amber-50 text-amber-700"
                                : "bg-gray-100 text-gray-500"
                              }`}>
                                {p.similarity > 0 ? `${Math.round(p.similarity * 100)}%` : "kw"}
                              </span>
                            </div>
                            <p className="text-xs text-gray-600 leading-relaxed line-clamp-2 group-hover:line-clamp-none transition-all">
                              {p.passage.slice(0, 180)}{p.passage.length > 180 ? "…" : ""}
                            </p>
                            {p.page_number && (
                              <p className="text-xs text-gray-400 mt-1.5 flex items-center gap-1">
                                <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <rect x="3" y="3" width="18" height="18" rx="2"/>
                                </svg>
                                Page {p.page_number}
                              </p>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  )}
                  {gIdx < groups.length - 1 && <div className="mx-4 border-b border-gray-100"/>}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {groups.length > 0 && (
        <div className="flex-shrink-0 border-t border-gray-100 px-4 py-2.5 bg-gray-50 flex items-center justify-between">
          <span className="text-xs text-gray-400">Showing all sessions for this doc</span>
          <button
            onClick={() => setCollapsed(groups.reduce((acc, _, i) => ({ ...acc, [i]: true }), {}))}
            className="text-xs text-gray-400 hover:text-gray-600 transition-colors">
            Collapse all
          </button>
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function DocViewerPage({ user }) {
  const location = useLocation();
  const navigate = useNavigate();

  const {
    docName  = "",
    docText  = "",
    file_url: fileUrl = null,
    file:    stateFile = null,
  } = location.state || {};

  const isPDF = docName.toLowerCase().endsWith(".pdf");

  // ── Token bootstrap ───────────────────────────────────────────────────────
  const tokenRef = useRef(null);
  const [tokenReady, setTokenReady] = useState(false);

  useEffect(() => {
    fetch(`${AUTH_URL}/`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => {
        tokenRef.current = d.access_token || "dev-token";
        setTokenReady(true);
      })
      .catch(() => {
        tokenRef.current = "dev-token";
        setTokenReady(true);
      });
  }, []);

  // ── Load chat history from Supabase ──────────────────────────────────────
  const [historyLoaded, setHistoryLoaded] = useState(false);

  useEffect(() => {
    if (!tokenReady || !docName) return;
    getChatHistory(docName, tokenRef.current)
      .then(({ messages: msgs, sources }) => {
        setMessages((msgs || []).filter((m) => !m.streaming && m.content));
        setSourcesHistory(sources || []);
        setHistoryLoaded(true);
      })
      .catch(() => setHistoryLoaded(true));
  }, [tokenReady, docName]);

  // ── PDF file resolution ──────────────────────────────────────────────────
  const [resolvedFileUrl, setResolvedFileUrl] = useState(fileUrl || null);
  const [manualFile, setManualFile]           = useState(null);
  const blobUrlRef   = useRef(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    if (!isPDF || resolvedFileUrl || !tokenReady) return;
    const fetchFileUrl = async () => {
      try {
        const res = await fetch(
          `${AUTH_URL}/documents/file-url?doc_name=${encodeURIComponent(docName)}`,
          { credentials: "include" }
        );
        if (res.ok) {
          const data = await res.json();
          if (data.file_url) setResolvedFileUrl(data.file_url);
        }
      } catch (err) {
        console.warn("[PDF] Could not fetch file_url from backend:", err);
      }
    };
    fetchFileUrl();
  }, [isPDF, resolvedFileUrl, tokenReady, docName]);

  const handleManualFileSelect = (e) => {
    const file = e.target.files?.[0];
    if (!file || file.type !== "application/pdf") return;
    if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
    blobUrlRef.current = URL.createObjectURL(file);
    setManualFile(file);
  };

  useEffect(() => {
    if (isPDF || resolvedFileUrl || manualFile) return;
    if (blobUrlRef.current) return;
    const fileObj = (stateFile instanceof File ? stateFile : null) ?? retrieveFile();
    if (fileObj) {
      blobUrlRef.current = URL.createObjectURL(fileObj);
    }
  }, [isPDF, resolvedFileUrl, stateFile, manualFile]);

  const pdfFile = useMemo(() => {
    if (!isPDF) return null;
    if (resolvedFileUrl) return { url: resolvedFileUrl };
    if (manualFile && blobUrlRef.current) return { url: blobUrlRef.current };
    return blobUrlRef.current ? { url: blobUrlRef.current } : null;
  }, [isPDF, resolvedFileUrl, manualFile]);

  useEffect(() => {
    return () => {
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, []);

  // ── Chat / sources state ─────────────────────────────────────────────────
  const [messages,        setMessages]        = useState([]);
  const [sourcesHistory,  setSourcesHistory]  = useState([]);
  const [chatHistory,     setChatHistory]     = useState([]);
  const [input,           setInput]           = useState("");
  const [loading,         setLoading]         = useState(false);
  const [highlights,      setHighlights]      = useState([]);
  const [highlightedPages,setHighlightedPages]= useState({});
  const [numPages,        setNumPages]        = useState(null);
  const [fetchedText,     setFetchedText]     = useState("");
  const [selectedText,    setSelectedText]    = useState("");
  const [noteColor,       setNoteColor]       = useState(ANNOTATION_COLORS[0]);
  const [showNotePanel,   setShowNotePanel]   = useState(false);
  const [showSourcesPanel,setShowSourcesPanel]= useState(false);

  const pageRefs  = useRef({});
  const bottomRef = useRef(null);

  const sourcePassages = sourcesHistory.length > 0
    ? (sourcesHistory[sourcesHistory.length - 1].passages || [])
    : [];

  // ── Persist messages to Supabase (debounced) ─────────────────────────────
  useEffect(() => {
    const settled = messages.filter((m) => !m.streaming && m.content);
    if (!settled.length || !historyLoaded) return;

    const t = setTimeout(() => {
      saveChatHistory(docName, settled, sourcesHistory, tokenRef.current);
    }, 2000);
    return () => clearTimeout(t);
  }, [messages, sourcesHistory, historyLoaded, docName]);

  // ── Guard: redirect if no docName ────────────────────────────────────────
  useEffect(() => {
    if (!docName) navigate("/dashboard", { replace: true });
  }, [docName, navigate]);

  // ── Fetch document text ──────────────────────────────────────────────────
  useEffect(() => {
    if (!docName || !tokenReady) return;
    const t = setTimeout(async () => {
      try {
        const res = await getDocumentText(docName, tokenRef.current);
        setFetchedText(res.text || "");
      } catch {
        setFetchedText("");
      }
    }, 400);
    return () => clearTimeout(t);
  }, [docName, tokenReady]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const handleTextSelect = () => {
    if (isPDF) return;
    const sel = window.getSelection()?.toString().trim();
    if (sel && sel.length > 3) { setSelectedText(sel); setShowNotePanel(true); }
  };

  // ── Send question ────────────────────────────────────────────────────────
  const send = async () => {
    const question = input.trim();
    if (!question || loading || !tokenReady) return;

    const newHistory = [...chatHistory, { role: "user", content: question }];
    setChatHistory(newHistory);
    setMessages((m) => [
      ...m.filter((msg) => !msg.streaming),
      { role: "user", content: question },
      { role: "assistant", content: "", streaming: true },
    ]);
    setInput("");
    setLoading(true);
    setHighlights([]);
    setHighlightedPages({});

    try {
      await askQuestionStream(
        question,
        docName,
        newHistory,
        (token) => {
          setMessages((m) => {
            const updated = [...m];
            const last    = updated[updated.length - 1];
            updated[updated.length - 1] = { ...last, content: last.content + token };
            return updated;
          });
        },
        (sources, passages) => {
          setChatHistory((h) => [
            ...h,
            { role: "assistant", content: passages.length > 0 ? "..." : "Not found" },
          ]);
          setMessages((m) => {
            const updated = [...m];
            updated[updated.length - 1] = {
              ...updated[updated.length - 1],
              streaming: false,
              sources,
            };
            return updated;
          });

          const newGroup = {
            id:        `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
            question,
            passages:  passages || [],
            timestamp: Date.now(),
          };
          setSourcesHistory((prev) => [...prev, newGroup]);

          const phrases = [...new Set(
            (passages || [])
              .sort((a, b) => b.similarity - a.similarity)
              .map((p) => p.passage.slice(0, 120))
          )];
          setHighlights(phrases);

          if (isPDF && passages?.length > 0) {
            const sorted = [...passages].sort((a, b) => b.similarity - a.similarity);
            const pageH  = {};
            sorted.forEach((p) => {
              const pg = p.page_number || 1;
              if (!pageH[pg]) pageH[pg] = [];
              pageH[pg].push(p.passage.slice(0, 120));
            });
            setHighlightedPages(pageH);
            setTimeout(() => {
              const el = pageRefs.current[sorted[0].page_number || 1];
              if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
            }, 400);
          }

          setLoading(false);
        },
        3,
      );
    } catch (err) {
      setMessages((m) => {
        const updated = [...m];
        updated[updated.length - 1] = {
          ...updated[updated.length - 1],
          content:   `Error: ${err.message}`,
          streaming: false,
        };
        return updated;
      });
      setLoading(false);
    }
  };

  const displayText      = docText || fetchedText;
  const parts            = highlightText(displayText, highlights);
  const totalSourceCount = sourcesHistory.reduce((n, g) => n + (g.passages?.length || 0), 0);

  return (
    <div className="min-h-screen flex flex-col bg-gray-50 font-sans">
      <Navbar user={user} />

      <main className="flex overflow-hidden" style={{ height: "calc(100vh - 57px)" }}>

        {/* ── Document pane ────────────────────────────────────────────────── */}
        <div className="flex-1 flex flex-col overflow-hidden border-r border-gray-100 bg-white min-w-0">

          {/* Header */}
          <div className="flex items-center justify-between px-5 py-2.5 border-b border-gray-100 bg-white flex-shrink-0">
            <div className="flex items-center gap-3 min-w-0">
              <button onClick={() => navigate("/dashboard")}
                className="text-xs text-gray-400 hover:text-gray-600 transition-colors flex-shrink-0">
                ← Dashboard
              </button>
              <span className="text-gray-200">|</span>
              <span className="text-sm font-medium text-gray-700 truncate">{docName}</span>
              <span className={`text-xs px-2 py-0.5 rounded-full flex-shrink-0 ${isPDF ? "bg-red-50 text-red-600" : "bg-blue-50 text-blue-600"}`}>
                {isPDF ? "PDF" : "TXT"}
              </span>
            </div>

            <div className="flex items-center gap-2 flex-shrink-0">
              {sourcesHistory.length > 0 && (
                <button
                  onClick={() => setShowSourcesPanel((v) => !v)}
                  className={`text-xs px-2.5 py-1 rounded-lg flex items-center gap-1.5 transition-colors border ${
                    showSourcesPanel
                      ? "bg-amber-100 text-amber-800 border-amber-300"
                      : "bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100"
                  }`}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                    <polyline points="14 2 14 8 20 8"/>
                  </svg>
                  {totalSourceCount} source{totalSourceCount !== 1 ? "s" : ""}
                </button>
              )}
            </div>
          </div>

          {/* Document content */}
          <div
            onMouseUp={!isPDF ? handleTextSelect : undefined}
            className="flex-1 overflow-y-auto"
            style={{ position: "relative" }}
          >
            {isPDF ? (
              <div className="flex justify-center py-4 px-4">
                {pdfFile ? (
                  <Document
                    file={pdfFile}
                    onLoadSuccess={({ numPages: n }) => setNumPages(n)}
                    onLoadError={(err) => console.error("PDF load error:", err)}
                  >
                    {Array.from({ length: numPages || 1 }, (_, i) => {
                      const pageNum = i + 1;
                      return (
                        <div key={pageNum} ref={(el) => { pageRefs.current[pageNum] = el; }} className="relative mb-4">
                          <Page
                            pageNumber={pageNum}
                            width={Math.min(600, window.innerWidth - 60)}
                            className="border border-gray-100 rounded-lg overflow-hidden"
                            onRenderSuccess={() => {
                              if (!highlightedPages[pageNum]?.length) return;
                              const pageEl = pageRefs.current[pageNum];
                              if (!pageEl) return;
                              pageEl.querySelectorAll(".react-pdf__Page__textContent span").forEach((span) => {
                                const matched = highlightedPages[pageNum].some((phrase) =>
                                  span.textContent.toLowerCase().includes(phrase.toLowerCase().slice(0, 40))
                                );
                                if (matched) {
                                  span.style.backgroundColor = "rgba(254, 240, 138, 0.7)";
                                  span.style.borderRadius    = "2px";
                                }
                              });
                            }}
                          />
                        </div>
                      );
                    })}
                  </Document>
                ) : (
                  <div className="flex flex-col items-center justify-center pt-20 text-center px-6">
                    <div className="w-14 h-14 bg-gray-100 rounded-2xl flex items-center justify-center mb-4">
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth="1.5" strokeLinecap="round">
                        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                        <polyline points="14 2 14 8 20 8"/>
                      </svg>
                    </div>
                    <p className="text-sm text-gray-500 mb-1">
                      {tokenReady ? "PDF could not be loaded from storage." : "Loading PDF…"}
                    </p>
                    <p className="text-xs text-gray-400 mb-4">
                      {tokenReady
                        ? "The file URL could not be resolved."
                        : "Fetching document URL from storage…"}
                    </p>
                    {tokenReady && (
                      <>
                        <input
                          type="file"
                          accept="application/pdf"
                          ref={fileInputRef}
                          onChange={handleManualFileSelect}
                          className="hidden"
                        />
                        <button
                          onClick={() => fileInputRef.current?.click()}
                          className="px-4 py-2 text-white text-xs rounded-xl bg-[#185FA5] hover:bg-[#0C447C] transition-colors mb-2">
                          Open PDF from device
                        </button>
                        <p className="text-xs text-gray-400">
                          Or re-upload the document from the dashboard to fix storage permanently.
                        </p>
                      </>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div className="px-8 py-6 max-w-prose mx-auto">
                {displayText ? (
                  <p className="text-sm text-gray-800 leading-8 whitespace-pre-wrap select-text">
                    {parts.map((part, i) =>
                      part.highlight ? (
                        <mark key={i} style={{ backgroundColor: HIGHLIGHT_COLOR }} className="rounded px-0.5">{part.text}</mark>
                      ) : (
                        <span key={i}>{part.text}</span>
                      )
                    )}
                  </p>
                ) : (
                  <div className="flex flex-col items-center justify-center pt-20 text-center">
                    <div className="w-14 h-14 bg-gray-100 rounded-2xl flex items-center justify-center mb-4">
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth="1.5" strokeLinecap="round">
                        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                        <polyline points="14 2 14 8 20 8"/>
                      </svg>
                    </div>
                    <p className="text-sm text-gray-400 mb-3">Loading document...</p>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Note panel (text selection) */}
          {showNotePanel && (
            <div className="flex-shrink-0 border-t border-gray-200 bg-white px-5 py-4">
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs font-medium text-gray-700">
                  Add note for:{" "}
                  <span className="text-gray-500 italic">
                    "{selectedText.slice(0, 50)}{selectedText.length > 50 ? "..." : ""}"
                  </span>
                </p>
                <button onClick={() => { setShowNotePanel(false); setSelectedText(""); }}
                  className="text-xs text-gray-400 hover:text-gray-600">Cancel</button>
              </div>
              <div className="flex items-center gap-2 mb-3">
                <span className="text-xs text-gray-400">Color:</span>
                {ANNOTATION_COLORS.map((c) => (
                  <button key={c} onClick={() => setNoteColor(c)} style={{ backgroundColor: c }}
                    className={`w-5 h-5 rounded-full border-2 transition-transform ${noteColor === c ? "border-gray-600 scale-110" : "border-transparent"}`}/>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* ── Sources history panel (slides in) ────────────────────────────── */}
        {showSourcesPanel && (
          <SourcesPanel sourcesHistory={sourcesHistory} isPDF={isPDF}
            pageRefs={pageRefs} onClose={() => setShowSourcesPanel(false)}/>
        )}

        {/* ── Chat panel ───────────────────────────────────────────────────── */}
        <div className="w-96 flex flex-col bg-white flex-shrink-0 border-l border-gray-100">

          {/* Chat header */}
          <div className="px-4 py-3 border-b border-gray-100 bg-white flex-shrink-0">
            <div className="flex items-center justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <p className="text-sm font-semibold text-gray-900">Ask AI</p>
                  <button
                    onClick={() => {
                      saveChatHistory(docName, [], [], tokenRef.current);
                      setMessages([]); setSourcesHistory([]); setChatHistory([]);
                      setHighlights([]); setHighlightedPages({});
                    }}
                    className="text-xs text-gray-400 hover:text-red-400 transition-colors">
                    Clear
                  </button>
                </div>
                <p className="text-xs text-gray-400">
                  {isPDF ? "Searches embedded content" : "Highlights relevant passages"}
                </p>
              </div>
            </div>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-4">
            {!historyLoaded ? (
              <div className="flex items-center justify-center mt-10 gap-2 text-gray-400">
                <svg className="animate-spin" width="14" height="14" viewBox="0 0 24 24"
                  fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                  <path d="M21 12a9 9 0 11-6.219-8.56"/>
                </svg>
                <span className="text-xs">Loading chat history…</span>
              </div>
            ) : messages.length === 0 ? (
              <div className="text-center mt-10">
                <div className="w-10 h-10 rounded-xl flex items-center justify-center mx-auto mb-3 bg-blue-50">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                    stroke="#185FA5" strokeWidth="2" strokeLinecap="round">
                    <circle cx="11" cy="11" r="8"/>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"/>
                  </svg>
                </div>
                <p className="text-xs text-gray-400 leading-relaxed px-4">
                  Ask anything. Matching passages are highlighted automatically.
                </p>
              </div>
            ) : null}

            {messages.map((msg, i) => (
              <div key={i} className={`flex gap-2 ${msg.role === "user" ? "flex-row-reverse" : ""}`}>
                {msg.role === "assistant" && (
                  <div className="w-6 h-6 rounded-lg bg-[#185FA5] flex items-center justify-center flex-shrink-0 mt-0.5">
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="white">
                      <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
                    </svg>
                  </div>
                )}
                <div
                  className={`rounded-2xl px-3 py-2.5 text-xs leading-relaxed max-w-[82%] ${
                    msg.role === "user"
                      ? "bg-[#185FA5] text-white rounded-tr-sm"
                      : "bg-gray-50 border border-gray-100 text-gray-800 rounded-tl-sm"
                  }`}
                  style={{ wordBreak: "break-word", overflowWrap: "anywhere" }}
                >
                  {msg.content}
                  {msg.streaming && <span className="inline-block w-1 h-3 bg-gray-400 ml-0.5 animate-pulse rounded"/>}
                </div>
              </div>
            ))}

            {loading && !messages[messages.length - 1]?.streaming && (
              <div className="flex gap-2">
                <div className="w-6 h-6 rounded-lg bg-[#185FA5] flex items-center justify-center flex-shrink-0">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="white">
                    <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
                  </svg>
                </div>
                <div className="bg-gray-50 border border-gray-100 rounded-2xl rounded-tl-sm px-3 py-2.5 flex gap-1">
                  {[0, 150, 300].map((d) => (
                    <span key={d} className="w-1.5 h-1.5 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }}/>
                  ))}
                </div>
              </div>
            )}
            <div ref={bottomRef}/>
          </div>

          {/* Latest sources strip */}
          {sourcePassages.length > 0 && (
            <div className="flex-shrink-0 border-t border-gray-100 px-4 py-3 bg-gray-50">
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs font-medium text-gray-500">Latest sources ({sourcePassages.length})</p>
                <button onClick={() => setShowSourcesPanel((v) => !v)}
                  className={`text-xs transition-colors flex items-center gap-1 ${
                    showSourcesPanel ? "text-amber-700 font-medium" : "text-gray-400 hover:text-amber-600"
                  }`}>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/>
                    <polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
                  </svg>
                  {showSourcesPanel ? "Close history" : `All history (${sourcesHistory.length})`}
                </button>
              </div>
              <div className="flex flex-col gap-2 max-h-36 overflow-y-auto">
                {sourcePassages.map((p, i) => (
                  <div key={i}
                    onClick={() => {
                      if (!isPDF) {
                        const marks = document.querySelectorAll("mark");
                        if (marks[i]) marks[i].scrollIntoView({ behavior: "smooth", block: "center" });
                      } else {
                        const el = pageRefs.current[p.page_number || 1];
                        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
                      }
                    }}
                    className="bg-white border border-yellow-200 rounded-lg px-3 py-2 cursor-pointer hover:border-yellow-400 transition-colors">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium text-gray-600 truncate">{p.doc_name}</span>
                      <span className="text-xs text-yellow-600 ml-2 flex-shrink-0">
                        {p.similarity > 0 ? `${Math.round(p.similarity * 100)}% match` : "keyword match"}
                      </span>
                    </div>
                    <p className="text-xs text-gray-500 leading-relaxed line-clamp-2">{p.passage.slice(0, 120)}...</p>
                    {p.page_number && <p className="text-xs text-gray-400 mt-1">Page {p.page_number}</p>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Input */}
          <div className="flex-shrink-0 px-4 py-3 border-t border-gray-100">
            <div className="flex gap-2">
              <input type="text" value={input} onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && send()}
                placeholder="Ask about this document..."
                disabled={!tokenReady || !historyLoaded}
                className="flex-1 border border-gray-200 rounded-xl px-3 py-2 text-xs focus:outline-none focus:border-[#185FA5] transition-colors disabled:opacity-50"/>
              <button onClick={send} disabled={!input.trim() || loading || !tokenReady || !historyLoaded}
                className="px-3 rounded-xl text-white bg-[#185FA5] hover:bg-[#0C447C] disabled:opacity-40 transition-colors">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round">
                  <line x1="22" y1="2" x2="11" y2="13"/>
                  <polygon points="22 2 15 22 11 13 2 9 22 2"/>
                </svg>
              </button>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}