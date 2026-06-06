import { useNavigate } from "react-router-dom";
const AUTH_URL = import.meta.env.VITE_BACKEND_URL;

export default function OnboardingPage() {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen flex flex-col bg-white font-sans">
      <nav className="flex items-center px-8 py-3.5 border-b border-gray-100">
        <div className="flex items-center gap-2 text-base font-semibold">
          <div className="w-7 h-7 bg-[#185FA5] rounded-lg flex items-center justify-center">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="white">
              <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
            </svg>
          </div>
          SecureStream
        </div>
      </nav>

      <main className="flex-1 flex flex-col items-center justify-center px-6 py-16">
        <h1 className="text-3xl font-bold text-gray-900 mb-2 text-center">
          Get started with SecureStream
        </h1>
        <p className="text-gray-400 text-sm mb-10 text-center max-w-sm">
          Upload your documents and query them instantly with AI. Secure, private, fast.
        </p>

        <div className="w-full max-w-sm flex flex-col gap-3">
          <a
            href={`${AUTH_URL}/login`}
            className="w-full py-3 text-sm rounded-xl bg-[#185FA5] text-white hover:bg-[#0C447C] transition-colors text-center font-medium"
          >
            Sign in to get started →
          </a>
          <button
            onClick={() => navigate("/")}
            className="w-full py-3 text-sm rounded-xl border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
          >
            Back to home
          </button>
        </div>

        <div className="mt-12 grid grid-cols-1 sm:grid-cols-3 gap-5 w-full max-w-2xl">
          {[
            {
              icon: (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#185FA5" strokeWidth="2" strokeLinecap="round">
                  <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                  <polyline points="17 8 12 3 7 8"/>
                  <line x1="12" y1="3" x2="12" y2="15"/>
                </svg>
              ),
              bg: "bg-blue-50",
              title: "Upload docs",
              desc: "PDF or TXT — ingested and embedded instantly",
            },
            {
              icon: (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#0F6E56" strokeWidth="2" strokeLinecap="round">
                  <circle cx="11" cy="11" r="8"/>
                  <line x1="21" y1="21" x2="16.65" y2="16.65"/>
                </svg>
              ),
              bg: "bg-emerald-50",
              title: "Ask AI",
              desc: "Natural language questions answered from your content",
            },
            {
              icon: (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="#185FA5">
                  <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>
                </svg>
              ),
              bg: "bg-violet-50",
              title: "Stays private",
              desc: "Your documents are scoped to your account only",
            },
          ].map((f) => (
            <div key={f.title} className="bg-white border border-gray-100 rounded-xl p-5">
              <div className={`w-9 h-9 ${f.bg} rounded-lg flex items-center justify-center mb-3`}>
                {f.icon}
              </div>
              <p className="text-sm font-semibold text-gray-900 mb-1">{f.title}</p>
              <p className="text-xs text-gray-400 leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}