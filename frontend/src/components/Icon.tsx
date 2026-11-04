// 16px line icons (24-unit grid, 1.5 stroke, currentColor), so they follow the theme.
const PATHS = {
  dashboard: <><rect x="3.5" y="3.5" width="7" height="8" rx="1" /><rect x="13.5" y="3.5" width="7" height="5" rx="1" /><rect x="13.5" y="11.5" width="7" height="9" rx="1" /><rect x="3.5" y="14.5" width="7" height="6" rx="1" /></>,
  sessions: <><path d="M4 6h9M4 12h16M4 18h6" /><circle cx="16.5" cy="6" r="2" /><circle cx="13.5" cy="18" r="2" /></>,
  logs: <path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01" />,
  projects: <path d="M3.5 7.5a2 2 0 0 1 2-2h3.5l2 2h7.5a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />,
  // A toothed cog, so it does not read as the theme toggle's sun. Path from Lucide's "settings" (ISC licence).
  settings: <><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" /><circle cx="12" cy="12" r="3" /></>,
  moon: <path d="M20 13.5A8 8 0 1 1 10.5 4a6.5 6.5 0 0 0 9.5 9.5z" />,
  sun: <><circle cx="12" cy="12" r="3.5" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4" /></>,
  panel: <><rect x="3.5" y="4.5" width="17" height="15" rx="1.5" /><path d="M9.5 4.5v15M15.5 10l-2 2 2 2" /></>,
  chevron: <path d="M8 10l4 4 4-4" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  refresh: <path d="M20 12a8 8 0 1 1-2.3-5.6M20 4.5v4h-4" />,
  signOut: <path d="M14 4.5h4a1.5 1.5 0 0 1 1.5 1.5v12a1.5 1.5 0 0 1-1.5 1.5h-4M10 16l4-4-4-4M14 12H4.5" />,
  info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8h.01" /></>,
}

export type IconName = keyof typeof PATHS

export function Icon({ name, className = '' }: { name: IconName; className?: string }) {
  return <svg className={`icon ${className}`} viewBox="0 0 24 24" aria-hidden="true" focusable="false">{PATHS[name]}</svg>
}
