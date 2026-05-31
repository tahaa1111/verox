import { Link, useLocation } from "react-router-dom";
import clsx from "clsx";
import { signOut } from "../firebase";
import { useStore } from "../store";

export function NavBar() {
  const { pathname } = useLocation();
  const setFirebaseToken = useStore((s) => s.setFirebaseToken);
  const setIsAdmin = useStore((s) => s.setIsAdmin);
  const isAdmin = useStore((s) => s.isAdmin);
  const jobQueue = useStore((s) => s.jobQueue);

  const handleSignOut = async () => {
    await signOut();
    setFirebaseToken(null);
    setIsAdmin(false);
  };

  // Regular operators see Camera + History. Admin users additionally see Admin.
  const links = [
    { to: "/", label: "Camera", exact: true },
    { to: "/upload", label: "Upload", exact: false },
    { to: "/history", label: "History", exact: false },
    ...(isAdmin ? [{ to: "/admin", label: "Admin", exact: false }] : []),
  ];

  return (
    <nav className="bg-white border-b border-gray-100 shadow-sm px-6 py-0 flex items-center gap-8 h-14">
      {/* Logo / brand */}
      <Link to="/" className="flex items-center gap-2 shrink-0">
        <div className="w-7 h-7 bg-brand-600 rounded-lg flex items-center justify-center">
          <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5}
              d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
          </svg>
        </div>
        <span className="font-bold text-gray-900 text-base tracking-tight">MediBox</span>
        <span className="hidden sm:inline text-xs text-gray-400 font-normal">/ OCR</span>
      </Link>

      {/* Navigation links */}
      <div className="flex items-center gap-1 text-sm ml-auto">
        {links.map((l) => {
          const active = l.exact ? pathname === l.to : pathname.startsWith(l.to);
          return (
            <Link
              key={l.to}
              to={l.to}
              className={clsx(
                "px-3 py-1.5 rounded-lg font-medium transition-colors",
                active
                  ? "bg-brand-50 text-brand-700"
                  : "text-gray-500 hover:text-gray-800 hover:bg-gray-100"
              )}
            >
              {l.label}
            </Link>
          );
        })}

        {/* Queue indicator — shows on every page when jobs are processing */}
        {jobQueue.length > 0 && (
          <Link
            to="/queue"
            title="View scan queue"
            className="ml-1 flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-brand-50 text-brand-700 font-medium text-sm hover:bg-brand-100 transition-colors"
          >
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-brand-500 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-brand-600" />
            </span>
            {jobQueue.length} in queue
          </Link>
        )}

        {/* Sign out */}
        <button
          onClick={handleSignOut}
          title="Sign out"
          className="ml-2 p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/>
          </svg>
        </button>
      </div>
    </nav>
  );
}
