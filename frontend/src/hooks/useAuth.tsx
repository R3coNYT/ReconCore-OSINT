/** Authentication context: session, role, sign-out. */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { api, tokens } from '@/lib/api'
import type { Role, TokenPair, User } from '@/types'

interface AuthState {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  can: (minimum: Role) => boolean
}

const HIERARCHY: Record<Role, number> = { READ_ONLY: 0, ANALYST: 1, ADMIN: 2 }

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!tokens.access() && !tokens.refresh()) {
      setLoading(false)
      return
    }
    api
      .get<User>('/auth/me')
      .then(setUser)
      .catch(() => tokens.clear())
      .finally(() => setLoading(false))
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const pair = await api.post<TokenPair>('/auth/login', { email, password })
    tokens.set(pair)
    setUser(await api.get<User>('/auth/me'))
  }, [])

  const logout = useCallback(async () => {
    const refresh = tokens.refresh()
    if (refresh) {
      await api.post('/auth/logout', { refresh_token: refresh }).catch(() => undefined)
    }
    tokens.clear()
    setUser(null)
  }, [])

  const can = useCallback(
    (minimum: Role) => (user ? HIERARCHY[user.role] >= HIERARCHY[minimum] : false),
    [user],
  )

  const value = useMemo(
    () => ({ user, loading, login, logout, can }),
    [user, loading, login, logout, can],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>')
  return context
}
