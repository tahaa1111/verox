import { Link, useLocation } from "react-router-dom";
import clsx from "clsx";

const links = [
  { to: "/submit", label: "Submit" },
  { to: "/admin", label: "Admin" },
];

export function NavBar() {
  const { pathname } = useLocation();
  return (
    <nav className="bg-brand-700 text-white px-4 py-3 flex items-center gap-6">
      <span className="font-bold text-lg tracking-tight">Medibox</span>
      <div className="flex gap-4 text-sm">
        {links.map((l) => (
          <Link
            key={l.to}
            to={l.to}
            className={clsx(
              "hover:text-brand-100 transition-colors",
              pathname.startsWith(l.to) && "underline text-white font-semibold"
            )}
          >
            {l.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}
