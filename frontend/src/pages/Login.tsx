import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { ErrorBox } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email, password)
      navigate('/')
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="h-full flex items-center justify-center p-6">
      <form onSubmit={submit} className="card w-full max-w-sm p-6 space-y-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">
            Recon<span className="text-accent">Core</span> OSINT
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Public-source investigation platform. Restricted, logged access.
          </p>
        </div>

        {error != null && <ErrorBox error={error} />}

        <div>
          <label className="label" htmlFor="email">Email</label>
          <input
            id="email"
            className="input"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>

        <div>
          <label className="label" htmlFor="password">Password</label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>

        <button className="btn btn-primary w-full justify-center" disabled={busy}>
          {busy ? 'Signing in...' : 'Sign in'}
        </button>

        <p className="text-[11px] text-slate-600 leading-relaxed">
          Every search is recorded (user, target, date). Use this tool only within
          a lawful framework and for documented purposes.
        </p>
      </form>
    </div>
  )
}
