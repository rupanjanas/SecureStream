import { useNavigate } from "react-router-dom";

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
          How will you use SecureStream?
        </h1>
        <p className="text-gray-400 text-sm mb-10 text-center">
          Choose how you'd like to get started
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5 w-full max-w-2xl">
          {/* Personal */}
          <button
            onClick={() => {
              window.location.href = "http://localhost:3000/login";
            }}
            className="bg-white border border-gray-200 rounded-2xl p-8 text-left hover:border-[#185FA5] hover:shadow-sm transition-all group"
          >
            <div className="w-12 h-12 bg-blue-50 rounded-xl flex items-center justify-center mb-4 group-hover:bg-blue-100 transition-colors">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
                stroke="#185FA5" strokeWidth="2" strokeLinecap="round">
                <circle cx="12" cy="8" r="4"/>
                <path d="M4 20c0-4 3.58-7 8-7s8 3 8 7"/>
              </svg>
            </div>
            <h2 className="text-base font-semibold text-gray-900 mb-1">Personal use</h2>
            <p className="text-sm text-gray-400 leading-relaxed">
              Upload and query your own documents. Your data stays private — no sharing.
            </p>
            <div className="mt-4 text-xs font-medium text-[#185FA5]">
              Get started free →
            </div>
          </button>

          {/* Organisation */}
          <button
            onClick={() => navigate("/org-setup")}
            className="bg-white border border-gray-200 rounded-2xl p-8 text-left hover:border-emerald-400 hover:shadow-sm transition-all group"
          >
            <div className="w-12 h-12 bg-emerald-50 rounded-xl flex items-center justify-center mb-4 group-hover:bg-emerald-100 transition-colors">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
                stroke="#0F6E56" strokeWidth="2" strokeLinecap="round">
                <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/>
                <circle cx="9" cy="7" r="4"/>
                <path d="M23 21v-2a4 4 0 00-3-3.87"/>
                <path d="M16 3.13a4 4 0 010 7.75"/>
              </svg>
            </div>
            <h2 className="text-base font-semibold text-gray-900 mb-1">Organisation</h2>
            <p className="text-sm text-gray-400 leading-relaxed">
              Collaborate with your team. Share documents, annotations, and AI insights across your org.
            </p>
            <div className="mt-4 text-xs font-medium text-emerald-600">
              Set up workspace →
            </div>
          </button>
        </div>

        <div className="mt-8 text-center">
          <p className="text-xs text-gray-400">
            Already have an invite link?{" "}
            <button
              onClick={() => {
                const link = prompt("Paste your invite link:");
                if (link) window.location.href = link;
              }}
              className="text-[#185FA5] hover:underline"
            >
              Join an organisation
            </button>
          </p>
        </div>
      </main>
    </div>
  );
}