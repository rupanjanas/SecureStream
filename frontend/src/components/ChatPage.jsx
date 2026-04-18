import { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import Navbar from "../components/Navbar";
import { askQuestion } from "../api/aiService";

function Message({ msg }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : "flex-row"}`}>
      {!isUser && (
        <div className="w-7 h-7 bg-[#185FA5] rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="white">
            <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
          </svg>
        </div>
      )}
      <div className="max-w-[75%] flex flex-col gap-2">
        <div className={`rounded-2xl px-4 py-3 text-sm leading-relaxed ${
          isUser
            ? "bg-[#185FA5] text-white rounded-tr-sm"
            : "bg-white border border-gray-100 text-gray-800 rounded-tl-sm"
        }`}>
          {msg.content}
        </div>
        {msg.sources?.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <p className="text-xs text-gray-400">Sources:</p>
            {msg.sources.map((s, i) => (
              <div key={i}
                className="text-xs bg-blue-50 border border-blue-100 rounded-lg px-3 py-2 text-gray-600 leading-relaxed">
                {s}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function ChatPage({ user }) {
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content: "Hi! I can answer questions about your uploaded documents using Llama 3. Upload a document first, then ask me anything about it.",
      sources: []
    }
  ]);
  const [input, setInput]     = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef             = useRef(null);
  const textareaRef           = useRef(null);
  const navigate              = useNavigate();

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const send = async () => {
    const question = input.trim();
    if (!question || loading) return;
    setMessages((m) => [...m, { role: "user", content: question, sources: [] }]);
    setInput("");
    setLoading(true);
    try {
      const data = await askQuestion(question);
      setMessages((m) => [...m, {
        role: "assistant",
        content: data.answer,
      }]);
    } catch (err) {
      setMessages((m) => [...m, {
        role: "assistant",
        content: `Something went wrong: ${err.message}. Make sure the AI service is running on port 8000 and Ollama is serving on port 11434.`,
        sources: []
      }]);
    } finally {
      setLoading(false);
    }
  };

  const handleKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="min-h-screen flex flex-col bg-gray-50 font-sans">
      <Navbar user={user} />
      <main className="flex-1 max-w-3xl mx-auto w-full px-6 py-6 flex flex-col" style={{ height: "calc(100vh - 57px)" }}>

        {/* Header */}
        <div className="flex items-center justify-between mb-4 flex-shrink-0">
          <div>
            <button onClick={() => navigate("/dashboard")}
              className="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1 mb-1">
              ← Dashboard
            </button>
            <h1 className="text-lg font-bold text-gray-900">Ask your documents</h1>
          </div>
          <button onClick={() => navigate("/upload")}
            className="px-3 py-1.5 text-xs rounded-lg border border-gray-200 hover:bg-white text-gray-600 transition-colors flex items-center gap-1.5">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="12" y1="5" x2="12" y2="19"/>
              <line x1="5" y1="12" x2="19" y2="12"/>
            </svg>
            Upload doc
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 bg-white border border-gray-100 rounded-2xl p-5 flex flex-col gap-5 overflow-y-auto mb-4">
          {messages.map((msg, i) => <Message key={i} msg={msg} />)}

          {loading && (
            <div className="flex gap-3 flex-row">
              <div className="w-7 h-7 bg-[#185FA5] rounded-lg flex items-center justify-center flex-shrink-0">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="white">
                  <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
                </svg>
              </div>
              <div className="bg-white border border-gray-100 rounded-2xl rounded-tl-sm px-4 py-3.5 flex gap-1.5 items-center">
                {[0, 150, 300].map((delay) => (
                  <span key={delay}
                    className="w-1.5 h-1.5 bg-gray-300 rounded-full animate-bounce"
                    style={{ animationDelay: `${delay}ms` }}
                  />
                ))}
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="flex-shrink-0">
          <div className="flex gap-2">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKey}
              placeholder="Ask a question about your documents..."
              rows={1}
              className="flex-1 resize-none rounded-xl border border-gray-200 px-4 py-3 text-sm focus:outline-none focus:border-[#185FA5] transition-colors bg-white"
              style={{ minHeight: "48px", maxHeight: "120px" }}
            />
            <button
              onClick={send}
              disabled={!input.trim() || loading}
              className="px-4 rounded-xl bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13"/>
                <polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </button>
          </div>
          <p className="text-xs text-gray-400 text-center mt-2">
            Enter to send · Shift+Enter for new line · Answers grounded in your documents only
          </p>
        </div>
      </main>
    </div>
  );
}