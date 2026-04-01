import { NavLink } from 'react-router-dom';
import { useAuth } from '../../auth/useAuth';
import {
  LayoutDashboard,
  PlusCircle,
  FileUp,
  GraduationCap,
  BarChart3,
  Settings,
  LogOut,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { useUIStore } from '../../stores/uiStore';
import NotificationBell from '../NotificationBell';

const NAV_ITEMS = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/add-job', icon: PlusCircle, label: 'Add Job' },
  { to: '/upload-resume', icon: FileUp, label: 'Upload Resume' },
];

const TOOL_ITEMS = [
  { to: '/interview-prep', icon: GraduationCap, label: 'Interview Prep' },
  { to: '/analytics', icon: BarChart3, label: 'Analytics' },
];

const ACCOUNT_ITEMS = [
  { to: '/settings', icon: Settings, label: 'Settings' },
];

function NavItem({ to, icon: Icon, label, collapsed }) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        `flex items-center gap-3 px-3 py-2.5 font-heading font-medium text-sm transition-all mb-0.5
        ${collapsed ? 'justify-center' : ''}
        ${
          isActive
            ? 'bg-yellow text-black border-2 border-black shadow-brutal-sm font-bold'
            : 'text-stone-500 border-2 border-transparent hover:border-black hover:text-black hover:bg-stone-100'
        }`
      }
      title={collapsed ? label : undefined}
    >
      <Icon size={18} strokeWidth={2.5} />
      {!collapsed && <span>{label}</span>}
    </NavLink>
  );
}

function SectionLabel({ children, collapsed }) {
  if (collapsed) return <div className="h-4" />;
  return (
    <p className="text-[10px] font-bold text-stone-400 uppercase tracking-widest px-3 pt-5 pb-1.5">
      {children}
    </p>
  );
}

export default function Sidebar() {
  const { user, signOut } = useAuth();
  const { sidebarCollapsed, toggleSidebar } = useUIStore();

  return (
    <aside
      className={`hidden md:flex flex-col bg-cream-dark border-r-2 border-black h-screen sticky top-0
        transition-all duration-200 ${sidebarCollapsed ? 'w-16' : 'w-56'}`}
    >
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-3 py-4 border-b-2 border-black">
        <div className="w-8 h-8 bg-black text-yellow font-mono font-bold text-sm flex items-center justify-center flex-shrink-0">
          N
        </div>
        {!sidebarCollapsed && (
          <span className="font-heading font-bold text-base tracking-tight text-black">
            NAUKRIBABA
          </span>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 px-2 py-2 overflow-y-auto">
        {NAV_ITEMS.map((item) => (
          <NavItem key={item.to} {...item} collapsed={sidebarCollapsed} />
        ))}

        <SectionLabel collapsed={sidebarCollapsed}>Tools</SectionLabel>
        {TOOL_ITEMS.map((item) => (
          <NavItem key={item.to} {...item} collapsed={sidebarCollapsed} />
        ))}

        <SectionLabel collapsed={sidebarCollapsed}>Account</SectionLabel>
        <NotificationBell collapsed={sidebarCollapsed} />
        {ACCOUNT_ITEMS.map((item) => (
          <NavItem key={item.to} {...item} collapsed={sidebarCollapsed} />
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t-2 border-black p-2">
        {/* Collapse toggle */}
        <button
          onClick={toggleSidebar}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 text-stone-400 hover:text-black transition-colors cursor-pointer"
          title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {sidebarCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          {!sidebarCollapsed && <span className="text-xs font-medium">Collapse</span>}
        </button>

        {/* User + Sign out */}
        {!sidebarCollapsed && user && (
          <div className="flex items-center justify-between px-3 py-2 mt-1">
            <span className="text-xs text-stone-500 truncate max-w-[120px]">
              {user.email}
            </span>
            <button
              onClick={signOut}
              className="text-stone-400 hover:text-error transition-colors cursor-pointer"
              title="Sign out"
            >
              <LogOut size={14} />
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
