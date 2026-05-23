import { Link, useLocation } from "react-router-dom";
import clsx from "clsx";

const links = [
  { to: "/", label: "Camera", exact: true },
  { to: "/admin", label: "Admin", exact: false },
];

export function NavBar() {
  const { pathname } = useLocation();
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
      </div>
    </nav>
  );
}
