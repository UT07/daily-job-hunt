import { NavLink } from 'react-router-dom';
import { LayoutDashboard, PlusCircle, Menu } from 'lucide-react';

// "Prep" (/interview-prep) and "Stats" (/analytics) used to point at dead
// `-- coming soon` stubs (audit P0-2) — removed, see App.jsx.
const ITEMS = [
  { to: '/', icon: LayoutDashboard, label: 'Home' },
  { to: '/add-job', icon: PlusCircle, label: 'Add' },
  { to: '/settings', icon: Menu, label: 'More' },
];

export default function MobileNav() {
  return (
    <nav className="fixed bottom-0 left-0 right-0 bg-cream border-t-2 border-black flex justify-around py-1.5 px-2 md:hidden z-50">
      {ITEMS.map(({ to, icon: Icon, label }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          className={({ isActive }) =>
            `flex flex-col items-center gap-0.5 px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider transition-colors
            ${isActive ? 'text-black bg-yellow border-2 border-black' : 'text-stone-400 border-2 border-transparent'}`
          }
        >
          <Icon size={18} strokeWidth={2.5} />
          {label}
        </NavLink>
      ))}
    </nav>
  );
}
