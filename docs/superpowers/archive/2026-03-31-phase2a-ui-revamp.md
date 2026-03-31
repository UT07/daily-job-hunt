# Phase 2A: Neo-Brutalist UI Revamp — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current dark-theme dashboard with a Neo-Brutalist Light design system — cream backgrounds, thick black borders, yellow accents, Space Grotesk + JetBrains Mono fonts, sidebar layout with job workspace.

**Architecture:** Tailwind v4 CSS-first `@theme` for design tokens. React Router v7 layout routes (`AppLayout` with sidebar + `<Outlet>`). Zustand for UI state (sidebar collapse, filters). All existing API calls and auth unchanged — this is a pure visual/structural rewrite.

**Tech Stack:** React 19, Vite 8, Tailwind CSS v4.2, React Router v7, Zustand, Lucide React (icons)

---

## File Structure

### New files to create:

```
web/src/
  layouts/
    AppLayout.jsx          — Sidebar + header + <Outlet> for authenticated pages
    AuthLayout.jsx         — Centered card layout for login/onboarding
  components/ui/
    Button.jsx             — Primary (black), secondary (outlined), accent (yellow) variants
    Card.jsx               — Brutalist card with thick border + shadow
    Badge.jsx              — Status badges (New, Applied, Interview, Offer, Rejected)
    Input.jsx              — Text input + textarea with brutalist focus style
    KPICard.jsx            — Metric card with label + monospace value + delta
    Tabs.jsx               — Horizontal tabs for job workspace
  components/layout/
    Sidebar.jsx            — Fixed sidebar with nav items + user pill
    MobileNav.jsx          — Bottom nav bar for mobile (<md breakpoint)
  pages/
    JobWorkspace.jsx       — Tabbed job detail page (replaces standalone result cards)
    AddJob.jsx             — Paste JD form (replaces tailor page inline form)
```

### Existing files to modify:

```
web/index.html                    — Update fonts (Space Grotesk + JetBrains Mono), title, favicon
web/src/index.css                 — Complete rewrite: @theme tokens, brutalist utilities
web/src/main.jsx                  — No changes (already clean)
web/src/App.jsx                   — Replace with router using layout routes
web/src/pages/Dashboard.jsx       — Restyle with brutalist design, use AppLayout
web/src/pages/LoginPage.jsx       — Restyle with brutalist light theme
web/src/pages/Settings.jsx        — Restyle
web/src/pages/Onboarding.jsx      — Restyle
web/src/pages/Privacy.jsx         — Restyle
web/src/pages/DataExport.jsx      — Restyle
web/src/components/JobTable.jsx   — Restyle: black header, cream rows, monospace scores
web/src/components/StatsBar.jsx   — Replace with KPICard-based layout
web/src/components/StatusDropdown.jsx — Restyle
web/src/components/ScoreCard.jsx  — Restyle for brutalist
web/src/components/TailorCard.jsx — Restyle for brutalist
web/src/components/CoverLetterCard.jsx — Restyle for brutalist
web/src/components/ContactsCard.jsx — Restyle for brutalist
web/src/components/ErrorBanner.jsx — Restyle for brutalist
web/src/components/ConsentBanner.jsx — Restyle for brutalist
```

### Files to delete:

```
web/src/components/ScoreBadge.jsx  — Functionality moved into Badge.jsx
web/src/components/AIQualityStats.jsx — Deferred to Phase 2G (Analytics)
```

---

## Dependency Graph

```
Task 1 (Install deps)
  ↓
Task 2 (Design tokens + index.css)
  ↓
Task 3 (UI primitives: Button, Card, Badge, Input, KPICard, Tabs)
  ↓
Task 4 (Sidebar + MobileNav)
  ↓
Task 5 (AppLayout + AuthLayout + Router rewrite)
  ↓
Task 6 (LoginPage restyle) — can parallel with 7-10
Task 7 (Dashboard restyle) — depends on 5
Task 8 (JobTable restyle) — depends on 3
Task 9 (AddJob page) — depends on 5
Task 10 (JobWorkspace page) — depends on 5, 8
Task 11 (Settings + Onboarding restyle) — depends on 5
Task 12 (Privacy + DataExport + ConsentBanner restyle) — depends on 5
Task 13 (Result cards restyle: Score, Tailor, CoverLetter, Contacts, Error) — depends on 3
Task 14 (Responsive: mobile nav, card-view tables) — depends on 4, 7, 8
Task 15 (Cleanup + final polish) — depends on all
```

---

## Task 1: Install Dependencies

**Files:**
- Modify: `web/package.json`

- [ ] **Step 1: Install zustand and lucide-react**

```bash
cd web && npm install zustand lucide-react
```

- [ ] **Step 2: Verify build still works**

```bash
npm run build
```

Expected: Build succeeds with no errors.

- [ ] **Step 3: Commit**

```bash
git add web/package.json web/package-lock.json
git commit -m "chore: add zustand and lucide-react for UI revamp"
```

---

## Task 2: Design Tokens + index.css

**Files:**
- Modify: `web/index.html`
- Modify: `web/src/index.css`

- [ ] **Step 1: Update index.html — fonts, title, favicon**

Replace the Google Fonts link and title in `web/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" href="/favicon.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet" />
    <title>NaukriBaba — AI Job Search Command Center</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

- [ ] **Step 2: Create SVG favicon**

Create `web/public/favicon.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="6" fill="#1c1917"/>
  <text x="50%" y="55%" dominant-baseline="middle" text-anchor="middle" font-family="monospace" font-weight="700" font-size="18" fill="#fbbf24">N</text>
</svg>
```

- [ ] **Step 3: Rewrite index.css with @theme design tokens**

Replace the entire contents of `web/src/index.css`:

```css
@import "tailwindcss";

/* Google Fonts are loaded via index.html <link> tag */

@theme {
  /* === COLORS === */
  --color-cream: #fafaf9;
  --color-cream-dark: #f5f5f4;

  --color-stone-100: #f5f5f4;
  --color-stone-200: #e7e5e4;
  --color-stone-300: #d6d3d1;
  --color-stone-400: #a8a29e;
  --color-stone-500: #78716c;
  --color-stone-600: #57534e;
  --color-stone-700: #44403c;
  --color-stone-800: #292524;
  --color-stone-900: #1c1917;

  --color-black: #1c1917;
  --color-white: #ffffff;

  --color-yellow: #fbbf24;
  --color-yellow-light: #fef3c7;
  --color-yellow-dark: #f59e0b;

  --color-success: #16a34a;
  --color-success-light: #dcfce7;
  --color-error: #dc2626;
  --color-error-light: #fef2f2;
  --color-info: #2563eb;
  --color-info-light: #dbeafe;
  --color-warning: #ea580c;
  --color-warning-light: #fff7ed;

  /* === FONTS === */
  --font-heading: "Space Grotesk", system-ui, sans-serif;
  --font-body: "Space Grotesk", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;

  /* === SHADOWS (hard offset, zero blur) === */
  --shadow-brutal-sm: 2px 2px 0px 0px #1c1917;
  --shadow-brutal: 4px 4px 0px 0px #1c1917;
  --shadow-brutal-lg: 6px 6px 0px 0px #1c1917;
  --shadow-brutal-yellow: 4px 4px 0px 0px #fbbf24;
}

/* === BASE STYLES === */
body {
  font-family: "Space Grotesk", system-ui, sans-serif;
  margin: 0;
  background-color: #fafaf9;
  color: #1c1917;
}

/* === ANIMATIONS === */
@keyframes spin {
  to { transform: rotate(360deg); }
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

.animate-fade-in {
  animation: fadeIn 0.2s ease-out;
}

.spinner {
  border: 3px solid #e7e5e4;
  border-top-color: #1c1917;
  border-radius: 50%;
  width: 18px;
  height: 18px;
  animation: spin 0.6s linear infinite;
  display: inline-block;
}

/* === SCROLLBAR (light theme) === */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: #f5f5f4;
}

::-webkit-scrollbar-thumb {
  background: #d6d3d1;
  border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
  background: #a8a29e;
}
```

- [ ] **Step 4: Verify build compiles with new tokens**

```bash
cd web && npm run build
```

Expected: Build succeeds. The tokens generate Tailwind classes like `bg-cream`, `text-black`, `shadow-brutal`, `font-heading`, etc.

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/public/favicon.svg web/src/index.css
git commit -m "feat: neo-brutalist design tokens and base styles"
```

---

## Task 3: UI Primitive Components

**Files:**
- Create: `web/src/components/ui/Button.jsx`
- Create: `web/src/components/ui/Card.jsx`
- Create: `web/src/components/ui/Badge.jsx`
- Create: `web/src/components/ui/Input.jsx`
- Create: `web/src/components/ui/KPICard.jsx`
- Create: `web/src/components/ui/Tabs.jsx`

- [ ] **Step 1: Create Button component**

Create `web/src/components/ui/Button.jsx`:

```jsx
const VARIANTS = {
  primary:
    'bg-black text-cream border-2 border-black shadow-brutal hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-brutal-sm active:translate-x-1 active:translate-y-1 active:shadow-none',
  secondary:
    'bg-cream text-black border-2 border-black shadow-brutal hover:bg-stone-100 hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-brutal-sm',
  accent:
    'bg-yellow text-black border-2 border-black shadow-brutal hover:bg-yellow-dark hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-brutal-sm',
  ghost:
    'bg-transparent text-stone-600 border-2 border-transparent hover:border-black hover:text-black',
  danger:
    'bg-error text-white border-2 border-black shadow-brutal hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-brutal-sm',
};

const SIZES = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-5 py-2.5 text-sm',
  lg: 'px-6 py-3 text-base',
};

export default function Button({
  variant = 'primary',
  size = 'md',
  disabled = false,
  loading = false,
  children,
  className = '',
  ...props
}) {
  return (
    <button
      disabled={disabled || loading}
      className={`font-heading font-bold transition-all cursor-pointer
        disabled:opacity-50 disabled:cursor-not-allowed disabled:translate-x-0 disabled:translate-y-0 disabled:shadow-brutal
        inline-flex items-center justify-center gap-2
        ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
      {...props}
    >
      {loading && <span className="spinner" />}
      {children}
    </button>
  );
}
```

- [ ] **Step 2: Create Card component**

Create `web/src/components/ui/Card.jsx`:

```jsx
export default function Card({ children, className = '', hover = false, ...props }) {
  return (
    <div
      className={`bg-white border-2 border-black shadow-brutal
        ${hover ? 'hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-brutal-sm transition-all cursor-pointer' : ''}
        ${className}`}
      {...props}
    >
      {children}
    </div>
  );
}

export function CardHeader({ children, className = '' }) {
  return (
    <div className={`px-5 py-4 border-b-2 border-black ${className}`}>
      {children}
    </div>
  );
}

export function CardBody({ children, className = '' }) {
  return <div className={`px-5 py-4 ${className}`}>{children}</div>;
}
```

- [ ] **Step 3: Create Badge component**

Create `web/src/components/ui/Badge.jsx`:

```jsx
const VARIANTS = {
  new: 'bg-info-light text-info border-info',
  applied: 'bg-yellow-light text-yellow-dark border-yellow-dark',
  interview: 'bg-success-light text-success border-success',
  offer: 'bg-success text-white border-success',
  rejected: 'bg-error-light text-error border-error',
  withdrawn: 'bg-stone-200 text-stone-600 border-stone-400',
  default: 'bg-stone-200 text-stone-700 border-stone-400',
};

const STATUS_MAP = {
  New: 'new',
  Applied: 'applied',
  Interview: 'interview',
  Offer: 'offer',
  Rejected: 'rejected',
  Withdrawn: 'withdrawn',
};

export default function Badge({ status, children, className = '' }) {
  const key = STATUS_MAP[status] || 'default';
  const v = VARIANTS[key];
  const label = children || status;

  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 font-mono text-[11px] font-bold
        uppercase tracking-wider border-2 ${v} ${className}`}
    >
      {label}
    </span>
  );
}

export function ScoreBadge({ score, className = '' }) {
  if (score == null || score === 0) {
    return <span className="text-stone-400 font-mono text-xs">--</span>;
  }
  const rounded = Math.round(score);
  return (
    <span
      className={`font-mono font-bold text-sm ${
        rounded >= 85 ? 'text-success' : rounded >= 60 ? 'text-yellow-dark' : 'text-error'
      } ${className}`}
    >
      {rounded}
    </span>
  );
}
```

- [ ] **Step 4: Create Input component**

Create `web/src/components/ui/Input.jsx`:

```jsx
export default function Input({
  label,
  id,
  className = '',
  ...props
}) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');
  return (
    <div>
      {label && (
        <label
          htmlFor={inputId}
          className="block text-xs font-bold text-stone-500 uppercase tracking-wider mb-1.5"
        >
          {label}
        </label>
      )}
      <input
        id={inputId}
        className={`w-full bg-white border-2 border-black px-4 py-2.5 font-body text-sm text-black
          placeholder:text-stone-400
          focus:outline-none focus:shadow-brutal-yellow
          transition-shadow ${className}`}
        {...props}
      />
    </div>
  );
}

export function Textarea({ label, id, className = '', ...props }) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');
  return (
    <div>
      {label && (
        <label
          htmlFor={inputId}
          className="block text-xs font-bold text-stone-500 uppercase tracking-wider mb-1.5"
        >
          {label}
        </label>
      )}
      <textarea
        id={inputId}
        className={`w-full bg-white border-2 border-black px-4 py-3 font-body text-sm text-black
          placeholder:text-stone-400 resize-y
          focus:outline-none focus:shadow-brutal-yellow
          transition-shadow ${className}`}
        {...props}
      />
    </div>
  );
}

export function Select({ label, id, children, className = '', ...props }) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');
  return (
    <div>
      {label && (
        <label
          htmlFor={inputId}
          className="block text-xs font-bold text-stone-500 uppercase tracking-wider mb-1.5"
        >
          {label}
        </label>
      )}
      <select
        id={inputId}
        className={`w-full bg-white border-2 border-black px-4 py-2.5 font-body text-sm text-black
          focus:outline-none focus:shadow-brutal-yellow
          transition-shadow cursor-pointer ${className}`}
        {...props}
      >
        {children}
      </select>
    </div>
  );
}
```

- [ ] **Step 5: Create KPICard component**

Create `web/src/components/ui/KPICard.jsx`:

```jsx
export default function KPICard({ label, value, delta, deltaColor = 'text-success', icon }) {
  return (
    <div className="border-2 border-black bg-white p-5">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[11px] font-bold text-stone-400 uppercase tracking-wider">
            {label}
          </p>
          <p className="text-3xl font-bold font-mono text-black mt-1 tracking-tight">
            {value}
          </p>
          {delta && (
            <p className={`text-xs font-mono font-semibold mt-1 ${deltaColor}`}>
              {delta}
            </p>
          )}
        </div>
        {icon && (
          <div className="text-stone-400">{icon}</div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Create Tabs component**

Create `web/src/components/ui/Tabs.jsx`:

```jsx
export default function Tabs({ tabs, activeTab, onTabChange }) {
  return (
    <div className="flex border-b-2 border-black overflow-x-auto">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onTabChange(tab.id)}
          className={`px-5 py-3 text-sm font-heading font-bold whitespace-nowrap transition-colors cursor-pointer
            ${
              activeTab === tab.id
                ? 'bg-yellow text-black border-b-2 border-yellow -mb-[2px]'
                : 'text-stone-500 hover:text-black hover:bg-stone-100'
            }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 7: Verify build with all new components**

```bash
cd web && npm run build
```

Expected: Build succeeds (components aren't imported yet, but must have no syntax errors).

- [ ] **Step 8: Commit**

```bash
git add web/src/components/ui/
git commit -m "feat: brutalist UI primitive components (Button, Card, Badge, Input, KPICard, Tabs)"
```

---

## Task 4: Sidebar + Mobile Navigation

**Files:**
- Create: `web/src/components/layout/Sidebar.jsx`
- Create: `web/src/components/layout/MobileNav.jsx`
- Create: `web/src/stores/uiStore.js`

- [ ] **Step 1: Create UI store with Zustand**

Create `web/src/stores/uiStore.js`:

```js
import { create } from 'zustand';

export const useUIStore = create((set) => ({
  sidebarCollapsed: false,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
}));
```

- [ ] **Step 2: Create Sidebar component**

Create `web/src/components/layout/Sidebar.jsx`:

```jsx
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
```

- [ ] **Step 3: Create MobileNav component**

Create `web/src/components/layout/MobileNav.jsx`:

```jsx
import { NavLink } from 'react-router-dom';
import { LayoutDashboard, PlusCircle, GraduationCap, BarChart3, Menu } from 'lucide-react';

const ITEMS = [
  { to: '/', icon: LayoutDashboard, label: 'Home' },
  { to: '/add-job', icon: PlusCircle, label: 'Add' },
  { to: '/interview-prep', icon: GraduationCap, label: 'Prep' },
  { to: '/analytics', icon: BarChart3, label: 'Stats' },
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
```

- [ ] **Step 4: Verify build**

```bash
cd web && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add web/src/components/layout/ web/src/stores/
git commit -m "feat: sidebar navigation and mobile bottom nav"
```

---

## Task 5: AppLayout + AuthLayout + Router Rewrite

**Files:**
- Create: `web/src/layouts/AppLayout.jsx`
- Create: `web/src/layouts/AuthLayout.jsx`
- Modify: `web/src/App.jsx`

- [ ] **Step 1: Create AppLayout**

Create `web/src/layouts/AppLayout.jsx`:

```jsx
import { Outlet, Navigate } from 'react-router-dom';
import { useAuth } from '../auth/useAuth';
import Sidebar from '../components/layout/Sidebar';
import MobileNav from '../components/layout/MobileNav';
import ConsentBanner from '../components/ConsentBanner';

export default function AppLayout() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen bg-cream flex items-center justify-center">
        <span className="spinner" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return (
    <div className="flex min-h-screen bg-cream">
      <Sidebar />
      <main className="flex-1 p-6 pb-20 md:pb-6 overflow-auto">
        <Outlet />
      </main>
      <MobileNav />
      <ConsentBanner />
    </div>
  );
}
```

- [ ] **Step 2: Create AuthLayout**

Create `web/src/layouts/AuthLayout.jsx`:

```jsx
import { Outlet, Navigate } from 'react-router-dom';
import { useAuth } from '../auth/useAuth';

export default function AuthLayout() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen bg-cream flex items-center justify-center">
        <span className="spinner" />
      </div>
    );
  }

  if (user) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="min-h-screen bg-cream flex flex-col justify-center">
      <Outlet />
    </div>
  );
}
```

- [ ] **Step 3: Rewrite App.jsx with layout routes**

Replace the entire contents of `web/src/App.jsx`:

```jsx
import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import AuthProvider from './auth/AuthProvider';
import AppLayout from './layouts/AppLayout';
import AuthLayout from './layouts/AuthLayout';

// Lazy-loaded pages
const Dashboard = lazy(() => import('./pages/Dashboard'));
const AddJob = lazy(() => import('./pages/AddJob'));
const JobWorkspace = lazy(() => import('./pages/JobWorkspace'));
const Settings = lazy(() => import('./pages/Settings'));
const Onboarding = lazy(() => import('./pages/Onboarding'));
const Privacy = lazy(() => import('./pages/Privacy'));
const DataExport = lazy(() => import('./pages/DataExport'));
const LoginPage = lazy(() => import('./pages/LoginPage'));

// Placeholder pages (to be built in later tasks)
function UploadResume() {
  return <div className="font-heading text-stone-400">Upload Resume — coming soon</div>;
}
function InterviewPrep() {
  return <div className="font-heading text-stone-400">Interview Prep — coming soon</div>;
}
function Analytics() {
  return <div className="font-heading text-stone-400">Analytics — coming soon</div>;
}

function PageLoader() {
  return (
    <div className="flex items-center justify-center py-20">
      <span className="spinner" />
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Suspense fallback={<PageLoader />}>
          <Routes>
            {/* Auth pages */}
            <Route element={<AuthLayout />}>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/onboarding" element={<Onboarding />} />
            </Route>

            {/* App pages (sidebar layout) */}
            <Route element={<AppLayout />}>
              <Route index element={<Dashboard />} />
              <Route path="/jobs/:jobId" element={<JobWorkspace />} />
              <Route path="/add-job" element={<AddJob />} />
              <Route path="/upload-resume" element={<UploadResume />} />
              <Route path="/interview-prep" element={<InterviewPrep />} />
              <Route path="/analytics" element={<Analytics />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/privacy" element={<Privacy />} />
              <Route path="/data-export" element={<DataExport />} />
            </Route>
          </Routes>
        </Suspense>
      </AuthProvider>
    </BrowserRouter>
  );
}
```

- [ ] **Step 4: Create placeholder AddJob page**

Create `web/src/pages/AddJob.jsx`:

```jsx
export default function AddJob() {
  return <div className="font-heading text-stone-400">Add Job page — will be built in Task 9</div>;
}
```

- [ ] **Step 5: Create placeholder JobWorkspace page**

Create `web/src/pages/JobWorkspace.jsx`:

```jsx
export default function JobWorkspace() {
  return <div className="font-heading text-stone-400">Job Workspace — will be built in Task 10</div>;
}
```

- [ ] **Step 6: Verify build and test locally**

```bash
cd web && npm run build
```

Expected: Build succeeds. All routes work — login redirects unauthenticated users, app layout shows sidebar for authenticated users.

- [ ] **Step 7: Commit**

```bash
git add web/src/layouts/ web/src/App.jsx web/src/pages/AddJob.jsx web/src/pages/JobWorkspace.jsx
git commit -m "feat: app layout with sidebar, auth layout, lazy-loaded routes"
```

---

## Task 6: LoginPage Restyle

**Files:**
- Modify: `web/src/pages/LoginPage.jsx`

- [ ] **Step 1: Restyle LoginPage to brutalist light theme**

Replace the entire contents of `web/src/pages/LoginPage.jsx`:

```jsx
import { useState } from 'react';
import { useAuth } from '../auth/useAuth';
import Button from '../components/ui/Button';
import Input from '../components/ui/Input';

export default function LoginPage() {
  const { signIn, signUp, signInWithGoogle, resetPassword } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isSignUp, setIsSignUp] = useState(false);
  const [isForgot, setIsForgot] = useState(false);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (isForgot) {
        await resetPassword(email);
        setSuccess('Password reset link sent. Check your email.');
      } else if (isSignUp) {
        await signUp(email, password);
        setSuccess('Account created. Check your email to confirm, then sign in.');
      } else {
        await signIn(email, password);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleGoogle() {
    setError(null);
    try {
      await signInWithGoogle();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="max-w-md w-full mx-auto px-4">
      {/* Logo */}
      <div className="text-center mb-8">
        <div className="inline-flex items-center justify-center w-16 h-16 bg-black text-yellow font-mono font-bold text-2xl border-2 border-black shadow-brutal mb-4">
          N
        </div>
        <h1 className="text-3xl font-heading font-bold text-black tracking-tight">
          NAUKRIBABA
        </h1>
        <p className="text-sm text-stone-500 font-medium mt-1">
          AI Job Search Command Center
        </p>
      </div>

      {/* Card */}
      <div className="bg-white border-2 border-black shadow-brutal p-8">
        <h2 className="text-lg font-heading font-bold text-black mb-6 text-center">
          {isForgot ? 'Reset your password' : isSignUp ? 'Create an account' : 'Sign in'}
        </h2>

        {/* Google OAuth */}
        <button
          onClick={handleGoogle}
          className="w-full flex items-center justify-center gap-3 bg-white border-2 border-black
            px-4 py-2.5 text-sm font-heading font-bold text-black
            shadow-brutal-sm hover:translate-x-[1px] hover:translate-y-[1px] hover:shadow-none
            transition-all cursor-pointer"
        >
          <svg className="w-5 h-5" viewBox="0 0 24 24">
            <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
            <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
            <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
            <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
          </svg>
          Continue with Google
        </button>

        {/* Divider */}
        <div className="relative my-6">
          <div className="absolute inset-0 flex items-center">
            <div className="w-full border-t-2 border-stone-200" />
          </div>
          <div className="relative flex justify-center">
            <span className="bg-white px-3 text-[11px] font-bold text-stone-400 uppercase tracking-widest">
              or
            </span>
          </div>
        </div>

        {/* Messages */}
        {success && (
          <div className="mb-4 p-3 bg-success-light border-2 border-success text-sm text-success font-medium">
            {success}
          </div>
        )}
        {error && (
          <div className="mb-4 p-3 bg-error-light border-2 border-error text-sm text-error font-medium">
            {error}
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            label="Email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            placeholder="you@example.com"
          />
          {!isForgot && (
            <Input
              label="Password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={6}
              placeholder="At least 6 characters"
            />
          )}
          {!isForgot && !isSignUp && (
            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => { setIsForgot(true); setError(null); setSuccess(null); }}
                className="text-sm text-stone-500 hover:text-black font-medium transition-colors cursor-pointer"
              >
                Forgot password?
              </button>
            </div>
          )}
          <Button
            type="submit"
            variant="primary"
            size="lg"
            loading={loading}
            className="w-full"
          >
            {isForgot ? 'Send reset link' : isSignUp ? 'Create account' : 'Sign in'}
          </Button>
        </form>

        {/* Toggle */}
        <p className="mt-6 text-center text-sm text-stone-500">
          {isForgot ? (
            <button
              onClick={() => { setIsForgot(false); setError(null); setSuccess(null); }}
              className="font-bold text-black hover:underline cursor-pointer"
            >
              Back to sign in
            </button>
          ) : (
            <>
              {isSignUp ? 'Already have an account?' : "Don't have an account?"}{' '}
              <button
                onClick={() => { setIsSignUp(!isSignUp); setError(null); setSuccess(null); }}
                className="font-bold text-black hover:underline cursor-pointer"
              >
                {isSignUp ? 'Sign in' : 'Sign up'}
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

```bash
cd web && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/LoginPage.jsx
git commit -m "feat: brutalist login page"
```

---

## Task 7: Dashboard Restyle

**Files:**
- Modify: `web/src/pages/Dashboard.jsx`
- Modify: `web/src/components/StatsBar.jsx`

- [ ] **Step 1: Rewrite StatsBar to use KPICard**

Replace the entire contents of `web/src/components/StatsBar.jsx`:

```jsx
import { Briefcase, CheckCircle, Users, TrendingUp } from 'lucide-react';
import KPICard from './ui/KPICard';

export default function StatsBar({ stats }) {
  const avgScore = Math.round(stats.avg_match_score ?? 0);

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-0 border-2 border-black mb-6">
      <div className="border-r-2 border-black last:border-r-0">
        <KPICard
          label="Total Jobs"
          value={stats.total_jobs ?? 0}
          icon={<Briefcase size={20} />}
        />
      </div>
      <div className="border-r-2 border-black last:border-r-0">
        <KPICard
          label="Applied"
          value={stats.jobs_by_status?.Applied ?? 0}
          icon={<CheckCircle size={20} />}
        />
      </div>
      <div className="border-r-2 border-black last:border-r-0">
        <KPICard
          label="Interviews"
          value={stats.jobs_by_status?.Interview ?? 0}
          icon={<Users size={20} />}
        />
      </div>
      <div>
        <KPICard
          label="Avg Score"
          value={avgScore}
          deltaColor={avgScore >= 85 ? 'text-success' : avgScore >= 60 ? 'text-yellow-dark' : 'text-error'}
          icon={<TrendingUp size={20} />}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Restyle Dashboard page**

Replace the header, filter bar, and pagination sections of `web/src/pages/Dashboard.jsx`. The key changes are:
- Remove dark theme classes (`bg-slate-*`, `text-slate-*`)
- Use brutalist tokens (`bg-cream`, `border-2 border-black`, `font-heading`)
- Import `Button` and `Select` from `components/ui/`
- Remove the old inline header (sidebar now handles navigation)
- Use `Select` component for filter dropdowns

Full replacement of `web/src/pages/Dashboard.jsx` — keep all existing state and data fetching logic, only change the JSX return and imports. The component body (state, useEffect, fetch functions) stays identical. Only the return JSX changes to use brutalist styling:

- Replace `bg-slate-900` → `bg-cream`
- Replace `bg-slate-800` → `bg-white border-2 border-black`
- Replace `text-slate-*` → `text-stone-*` or `text-black`
- Replace `bg-blue-600` → use `<Button>` component
- Replace header with just a title + action buttons (sidebar handles nav)
- Remove footer (sidebar handles branding)
- Pipeline status bar: `bg-success-light border-2 border-success`
- Filter bar: `border-2 border-black` with `Select` components

**Note to implementer:** The full JSX is too long to include inline. Follow the V1 mockup at `.superpowers/brainstorm/24441-1774891039/content/brutalist-evolved.html` (the first "Neo-Brutalist Light" section) for exact styling. Key pattern: every container gets `border-2 border-black`, backgrounds are `bg-white` or `bg-cream`, text is `text-black` or `text-stone-*`, buttons use `<Button>` component.

- [ ] **Step 3: Verify build**

```bash
cd web && npm run build
```

- [ ] **Step 4: Test locally**

```bash
cd web && npm run dev
```

Open `http://localhost:5173` — verify dashboard renders with cream background, black borders, KPI cards, and job table. Sidebar should show active "Dashboard" nav item in yellow.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/Dashboard.jsx web/src/components/StatsBar.jsx
git commit -m "feat: brutalist dashboard with KPI cards and filter bar"
```

---

## Task 8: JobTable Restyle

**Files:**
- Modify: `web/src/components/JobTable.jsx`

- [ ] **Step 1: Restyle JobTable with brutalist design**

Key changes to `web/src/components/JobTable.jsx`:
- Replace `ScoreBadge` with `import { ScoreBadge } from './ui/Badge'`
- Table wrapper: `border-2 border-black` (no border-radius)
- Header row: `bg-black text-cream` with uppercase, tracked labels
- Body rows: `bg-white` with `border-b border-stone-200`, `hover:bg-yellow-light`
- Asset icons: Replace emoji `📄📝📋` with Lucide icons (`FileText`, `Mail`, `Users`) inside small black/cream squares
- Status dropdown: restyled (Task 8b)
- Contacts cell: restyle with brutalist borders
- Make job title clickable → navigates to `/jobs/${job.job_id}`

- [ ] **Step 2: Add navigation to job workspace**

Add `import { useNavigate } from 'react-router-dom'` and make the title cell clickable:

```jsx
const navigate = useNavigate();
// In the title cell:
<td
  className="px-4 py-3 font-heading font-bold text-black cursor-pointer hover:underline"
  onClick={() => navigate(`/jobs/${job.job_id}`)}
>
  {job.title || '--'}
</td>
```

- [ ] **Step 3: Verify build and test**

```bash
cd web && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add web/src/components/JobTable.jsx
git commit -m "feat: brutalist job table with clickable titles"
```

---

## Task 9: AddJob Page (Replaces Tailor Page)

**Files:**
- Modify: `web/src/pages/AddJob.jsx`

- [ ] **Step 1: Build AddJob page**

This replaces the old tailor functionality in `App.jsx`. It's a form to paste a JD and trigger the full pipeline. Uses the same `apiCall` function and endpoints (`/api/score`, `/api/tailor`, `/api/cover-letter`, `/api/contacts`).

Create `web/src/pages/AddJob.jsx` with:
- `<Textarea>` for job description
- `<Input>` for job title, company
- `<Select>` for resume type
- `<Button variant="accent">` for "Generate Everything" (runs score + tailor + cover letter + contacts in sequence)
- Results render below using the existing result card components (ScoreCard, TailorCard, etc.)
- Move the `run()`, `getPayload()`, `addResult()` logic from old `App.jsx/AppContent` into this page

- [ ] **Step 2: Verify build**

```bash
cd web && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/AddJob.jsx
git commit -m "feat: add job page with brutalist form"
```

---

## Task 10: JobWorkspace Page

**Files:**
- Modify: `web/src/pages/JobWorkspace.jsx`

- [ ] **Step 1: Build tabbed job workspace**

This page loads when clicking a job title in the table. It shows a tabbed view of all data for one job.

```jsx
import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { apiGet } from '../api';
import { ArrowLeft } from 'lucide-react';
import Tabs from '../components/ui/Tabs';
import Button from '../components/ui/Button';
import { ScoreBadge } from '../components/ui/Badge';
import Badge from '../components/ui/Badge';

const JOB_TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'research', label: 'Research' },
  { id: 'resume', label: 'Resume' },
  { id: 'cover-letter', label: 'Cover Letter' },
  { id: 'contacts', label: 'Contacts' },
  { id: 'prep', label: 'Interview Prep' },
];

export default function JobWorkspace() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('overview');

  useEffect(() => {
    async function load() {
      try {
        // Fetch job data from dashboard endpoint and find this job
        const data = await apiGet(`/api/dashboard/jobs?per_page=200`);
        const found = data.jobs?.find((j) => j.job_id === jobId);
        setJob(found || null);
      } catch (err) {
        console.error('Failed to load job:', err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [jobId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="spinner" />
      </div>
    );
  }

  if (!job) {
    return (
      <div className="border-2 border-black bg-white p-8 text-center">
        <p className="text-stone-500 font-heading">Job not found.</p>
        <Button variant="ghost" onClick={() => navigate('/')} className="mt-4">
          Back to Dashboard
        </Button>
      </div>
    );
  }

  return (
    <div>
      {/* Back button + header */}
      <div className="flex items-center gap-4 mb-4">
        <button
          onClick={() => navigate('/')}
          className="text-stone-400 hover:text-black transition-colors cursor-pointer"
        >
          <ArrowLeft size={20} />
        </button>
        <div className="flex-1">
          <h1 className="text-xl font-heading font-bold text-black tracking-tight">
            {job.title}
          </h1>
          <p className="text-sm text-stone-500">
            {job.company} {job.location && `· ${job.location}`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <ScoreBadge score={job.match_score} className="text-2xl" />
          <Badge status={job.application_status || 'New'} />
          {job.apply_url && job.apply_url !== 'Apply' && (
            <a href={job.apply_url} target="_blank" rel="noopener noreferrer">
              <Button variant="accent" size="sm">Apply</Button>
            </a>
          )}
        </div>
      </div>

      {/* Tabs */}
      <Tabs tabs={JOB_TABS} activeTab={activeTab} onTabChange={setActiveTab} />

      {/* Tab content */}
      <div className="border-2 border-t-0 border-black bg-white p-6 min-h-[300px]">
        {activeTab === 'overview' && (
          <div>
            <div className="grid grid-cols-3 gap-4 mb-6">
              <div className="border-2 border-black p-4">
                <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">ATS</p>
                <p className="text-2xl font-mono font-bold"><ScoreBadge score={job.ats_score} /></p>
              </div>
              <div className="border-2 border-black p-4">
                <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">Hiring Manager</p>
                <p className="text-2xl font-mono font-bold"><ScoreBadge score={job.hiring_manager_score} /></p>
              </div>
              <div className="border-2 border-black p-4">
                <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">Technical</p>
                <p className="text-2xl font-mono font-bold"><ScoreBadge score={job.tech_recruiter_score} /></p>
              </div>
            </div>
            {job.description && (
              <div>
                <h3 className="text-xs font-bold text-stone-400 uppercase tracking-wider mb-2">Job Description</h3>
                <p className="text-sm text-stone-700 leading-relaxed whitespace-pre-wrap">
                  {job.description.slice(0, 1000)}{job.description.length > 1000 ? '...' : ''}
                </p>
              </div>
            )}
          </div>
        )}
        {activeTab === 'resume' && (
          <div>
            {job.resume_s3_url ? (
              <div>
                <p className="text-sm text-stone-500 mb-4">AI Model: <span className="font-mono font-bold text-black">{job.tailoring_model || '--'}</span></p>
                <a href={job.resume_s3_url} target="_blank" rel="noopener noreferrer">
                  <Button variant="primary" size="sm">Download Resume PDF</Button>
                </a>
              </div>
            ) : (
              <p className="text-stone-400">No tailored resume yet.</p>
            )}
          </div>
        )}
        {activeTab === 'cover-letter' && (
          <div>
            {job.cover_letter_s3_url ? (
              <a href={job.cover_letter_s3_url} target="_blank" rel="noopener noreferrer">
                <Button variant="primary" size="sm">Download Cover Letter PDF</Button>
              </a>
            ) : (
              <p className="text-stone-400">No cover letter yet.</p>
            )}
          </div>
        )}
        {activeTab === 'contacts' && (
          <div>
            <p className="text-stone-400">Contacts view — coming in Phase 2A refinement.</p>
          </div>
        )}
        {activeTab === 'research' && (
          <div>
            <p className="text-stone-400">Company Research — coming in Phase 2D.</p>
          </div>
        )}
        {activeTab === 'prep' && (
          <div>
            <p className="text-stone-400">Interview Prep — coming in Phase 2F.</p>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

```bash
cd web && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/JobWorkspace.jsx
git commit -m "feat: job workspace with tabbed overview, resume, cover letter"
```

---

## Task 11: Settings + Onboarding Restyle

**Files:**
- Modify: `web/src/pages/Settings.jsx`
- Modify: `web/src/pages/Onboarding.jsx`

- [ ] **Step 1: Restyle Settings page**

Key changes:
- Replace dark slate theme with `bg-cream`, `border-2 border-black`
- Use `Input`, `Button`, `Card` components from `components/ui/`
- Remove standalone header (sidebar handles navigation)
- Form sections: each in a `Card` with `CardHeader` + `CardBody`

- [ ] **Step 2: Restyle Onboarding page**

Key changes:
- Replace dark theme with brutalist light
- Step indicator: numbered blocks with black borders, active step in yellow
- Form fields use `Input` components
- Buttons use `Button` component

- [ ] **Step 3: Verify build**

```bash
cd web && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/Settings.jsx web/src/pages/Onboarding.jsx
git commit -m "feat: brutalist settings and onboarding pages"
```

---

## Task 12: Privacy + DataExport + ConsentBanner Restyle

**Files:**
- Modify: `web/src/pages/Privacy.jsx`
- Modify: `web/src/pages/DataExport.jsx`
- Modify: `web/src/components/ConsentBanner.jsx`

- [ ] **Step 1: Restyle Privacy, DataExport, ConsentBanner**

Same pattern: replace dark slate classes with brutalist light tokens. Use `Card`, `Button` components. ConsentBanner gets `bg-yellow border-t-2 border-black` with black text.

- [ ] **Step 2: Verify build**

```bash
cd web && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/Privacy.jsx web/src/pages/DataExport.jsx web/src/components/ConsentBanner.jsx
git commit -m "feat: brutalist privacy, data export, consent banner"
```

---

## Task 13: Result Cards Restyle

**Files:**
- Modify: `web/src/components/ScoreCard.jsx`
- Modify: `web/src/components/TailorCard.jsx`
- Modify: `web/src/components/CoverLetterCard.jsx`
- Modify: `web/src/components/ContactsCard.jsx`
- Modify: `web/src/components/ErrorBanner.jsx`

- [ ] **Step 1: Restyle all result cards**

These cards appear on the AddJob page after running score/tailor/cover-letter/contacts. Apply brutalist styling:
- Each card: `border-2 border-black shadow-brutal bg-white p-5`
- Score values: `font-mono font-bold`
- Error banner: `bg-error-light border-2 border-error text-error`
- Download links: `<Button variant="secondary" size="sm">`

- [ ] **Step 2: Delete ScoreBadge.jsx**

`ScoreBadge` is now part of `Badge.jsx`. Delete the old standalone component.

```bash
rm web/src/components/ScoreBadge.jsx
```

Update any remaining imports of `ScoreBadge` to use `import { ScoreBadge } from './ui/Badge'`.

- [ ] **Step 3: Verify build**

```bash
cd web && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add web/src/components/ScoreCard.jsx web/src/components/TailorCard.jsx \
  web/src/components/CoverLetterCard.jsx web/src/components/ContactsCard.jsx \
  web/src/components/ErrorBanner.jsx
git rm web/src/components/ScoreBadge.jsx
git commit -m "feat: brutalist result cards, remove old ScoreBadge"
```

---

## Task 14: Responsive — Mobile Nav + Card-View Tables

**Files:**
- Modify: `web/src/components/JobTable.jsx`

- [ ] **Step 1: Add mobile card view for job table**

Add a responsive breakpoint: on screens < md, render jobs as stacked cards instead of a table.

```jsx
{/* Desktop: table */}
<div className="hidden md:block">
  {/* ... existing table JSX ... */}
</div>

{/* Mobile: card stack */}
<div className="md:hidden space-y-3">
  {sorted.map((job) => (
    <div
      key={job.job_id}
      className="bg-white border-2 border-black shadow-brutal-sm p-4 cursor-pointer hover:bg-yellow-light transition-colors"
      onClick={() => navigate(`/jobs/${job.job_id}`)}
    >
      <div className="flex justify-between items-start">
        <div>
          <p className="font-heading font-bold text-black">{job.title}</p>
          <p className="text-xs text-stone-500 mt-0.5">{job.company} · {job.location || 'Remote'}</p>
        </div>
        <ScoreBadge score={job.match_score} className="text-lg" />
      </div>
      <div className="flex items-center gap-2 mt-3">
        <Badge status={job.application_status || 'New'} />
        {job.tailoring_model && (
          <span className="text-[10px] font-mono text-stone-400">{job.tailoring_model}</span>
        )}
      </div>
    </div>
  ))}
</div>
```

- [ ] **Step 2: Verify mobile layout**

```bash
cd web && npm run dev
```

Open Chrome DevTools, switch to mobile viewport (375px). Verify:
- Bottom nav bar shows 5 items
- Sidebar is hidden
- Job table renders as cards
- Cards are tappable and navigate to job workspace

- [ ] **Step 3: Commit**

```bash
git add web/src/components/JobTable.jsx
git commit -m "feat: responsive mobile card view for job table"
```

---

## Task 15: Cleanup + Final Polish

**Files:**
- Delete: `web/src/components/AIQualityStats.jsx` (deferred to Phase 2G)
- Modify: `web/src/components/StatusDropdown.jsx` (restyle)

- [ ] **Step 1: Remove AIQualityStats import from Dashboard**

Remove the import and usage of `AIQualityStats` from `Dashboard.jsx`. This component will be rebuilt in Phase 2G (Analytics).

- [ ] **Step 2: Restyle StatusDropdown**

Apply brutalist styling to the dropdown:
- `border-2 border-black bg-white font-mono text-xs font-bold`
- Options use status colors

- [ ] **Step 3: Delete AIQualityStats.jsx**

```bash
git rm web/src/components/AIQualityStats.jsx
```

- [ ] **Step 4: Full build + visual review**

```bash
cd web && npm run build
```

Review all pages:
1. `/login` — brutalist login card on cream background
2. `/` — dashboard with sidebar, KPI bar, filter bar, job table
3. `/jobs/:id` — tabbed workspace with scores, resume download, cover letter
4. `/add-job` — paste JD form with result cards
5. `/settings` — brutalist settings form
6. Mobile — bottom nav, card tables

- [ ] **Step 5: Deploy to Netlify**

```bash
npx netlify deploy --prod --dir=dist
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: complete Phase 2A neo-brutalist UI revamp"
```

---

## Verification After Phase 2A

1. **Visual consistency**: Every page uses cream/white backgrounds, black borders, yellow accents, Space Grotesk font
2. **No emoji icons**: All icons are Lucide SVGs
3. **Sidebar works**: Active item yellow, collapse/expand, all nav items route correctly
4. **Mobile works**: Bottom nav shows, sidebar hidden, tables render as cards
5. **Data flows**: Dashboard loads real jobs from API, filters work, pagination works
6. **Auth works**: Login → dashboard → sign out → redirect to login
7. **Job workspace**: Click job → tabbed view → resume/cover letter download links work
8. **Add job**: Paste JD → score → results render below
9. **Build clean**: `npm run build` produces zero errors/warnings
