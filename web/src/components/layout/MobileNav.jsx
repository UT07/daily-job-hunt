import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { LayoutDashboard, PlusCircle, Menu, LogOut } from 'lucide-react';
import { useAuth } from '../../auth/useAuth';

// "Prep" (/interview-prep) and "Stats" (/analytics) used to point at dead
// `-- coming soon` stubs (audit P0-2) — removed, see App.jsx.
const ITEMS = [
  { to: '/', icon: LayoutDashboard, label: 'Home' },
  { to: '/add-job', icon: PlusCircle, label: 'Add' },
  { to: '/settings', icon: Menu, label: 'More' },
];

const ITEM_CLASS =
  'flex flex-col items-center gap-0.5 px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider transition-colors';

export default function MobileNav() {
  // Sign out lived only in the desktop Sidebar (hidden below md), so a phone
  // had no way to sign out at all. On success AuthProvider clears the user
  // and AppLayout redirects to /login; a failure is shown, not swallowed.
  const { signOut } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState(null);

  async function handleSignOut() {
    setSigningOut(true);
    setSignOutError(null);
    try {
      await signOut();
    } catch (e) {
      setSignOutError(e?.message || 'request failed');
    } finally {
      setSigningOut(false);
    }
  }

  return (
    <nav className="fixed bottom-0 left-0 right-0 bg-cream border-t-2 border-black flex justify-around py-1.5 px-2 md:hidden z-50">
      {ITEMS.map(({ to, icon: Icon, label }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          className={({ isActive }) =>
            `${ITEM_CLASS}
            ${isActive ? 'text-black bg-yellow border-2 border-black' : 'text-stone-400 border-2 border-transparent'}`
          }
        >
          <Icon size={18} strokeWidth={2.5} />
          {label}
        </NavLink>
      ))}
      <button
        type="button"
        onClick={handleSignOut}
        disabled={signingOut}
        aria-label={signOutError ? `Sign out failed: ${signOutError}. Tap to retry.` : 'Sign out'}
        title={signOutError ? `Sign out failed: ${signOutError}` : 'Sign out'}
        className={`${ITEM_CLASS} border-2 border-transparent cursor-pointer disabled:opacity-50
          ${signOutError ? 'text-error' : 'text-stone-400'}`}
      >
        <LogOut size={18} strokeWidth={2.5} />
        {signOutError ? 'Retry' : 'Out'}
      </button>
    </nav>
  );
}
