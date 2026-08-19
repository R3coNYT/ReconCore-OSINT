/** Application shell: navigation sidebar plus content area. */
import { NavLink, Outlet, useNavigate } from 'react-router-dom'

import { useAuth } from '@/hooks/useAuth'

interface Item {
  to: string
  label: string
  end?: boolean
}

interface Group {
  title: string
  items: Item[]
}

const GROUPS: Group[] = [
  { title: '', items: [{ to: '/', label: 'Dashboard', end: true }] },
  {
    title: 'Investigations',
    items: [
      { to: '/investigations', label: 'All' },
      { to: '/investigations?entity_type=PERSON', label: 'People' },
      { to: '/investigations?entity_type=ORGANIZATION', label: 'Organisations' },
      { to: '/investigations?entity_type=DOMAIN', label: 'Domains' },
    ],
  },
  {
    title: 'Search',
    items: [
      { to: '/search/username', label: 'Username' },
      { to: '/search/email', label: 'Email' },
      { to: '/search/phone', label: 'Phone' },
      { to: '/search/advanced', label: 'Advanced search' },
    ],
  },
  {
    title: 'OSINT',
    items: [
      { to: '/plugins', label: 'Plugins' },
      { to: '/sources', label: 'Sources' },
      { to: '/history', label: 'History' },
    ],
  },
  { title: 'System', items: [{ to: '/settings', label: 'Settings' }] },
]

export default function Layout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  return (
    <div className="flex h-full">
      <aside className="w-60 shrink-0 bg-base-800 border-r border-line flex flex-col">
        <div className="px-4 py-4 border-b border-line">
          <p className="font-semibold tracking-tight text-slate-100">
            Recon<span className="text-accent">Core</span>
          </p>
          <p className="text-[11px] uppercase tracking-widest text-slate-600">OSINT platform</p>
        </div>

        <nav className="flex-1 overflow-y-auto py-3">
          {GROUPS.map((group) => (
            <div key={group.title || 'root'} className="mb-4">
              {group.title && (
                <p className="px-4 pb-1 text-[10px] uppercase tracking-widest text-slate-600">
                  {group.title}
                </p>
              )}
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    `block px-4 py-1.5 text-sm border-l-2 ${
                      isActive
                        ? 'border-accent text-accent bg-accent/5'
                        : 'border-transparent text-slate-400 hover:text-slate-200 hover:bg-base-700'
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="border-t border-line p-3 text-xs">
          <p className="text-slate-300 truncate">{user?.email}</p>
          <p className="text-slate-600 mb-2">{user?.role}</p>
          <button
            className="btn w-full justify-center"
            onClick={async () => {
              await logout()
              navigate('/login')
            }}
          >
            Sign out
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">
        <div className="p-6 max-w-[1500px] mx-auto">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
