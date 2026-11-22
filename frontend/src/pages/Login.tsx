import { useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { useAuth, DEV_USERS, ROLE_LABEL } from '../auth/auth'
import { Brand } from '../layout/AppShell'
import { Button, ErrorBox, Field, Input, Honesty } from '../components/ui'
import { API_MODE } from '../api/client'

const CHAIN = ['Heat', 'Lot', 'Inspect', 'Passport', 'Seal', 'Gate', 'Recall']

export default function Login() {
  const { login } = useAuth()
  const [u, setU] = useState('op.ravi'); const [p, setP] = useState('autoq-dev')
  const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false)
  const go = async (user = u, pass = p) => { setBusy(true); setErr(null); try { await login(user, pass) } catch (e) { setErr(e) } finally { setBusy(false) } }
  return (
    <div className="grid min-h-full lg:grid-cols-[1.1fr_1fr]">
      <section className="grid-bg relative hidden flex-col justify-between overflow-hidden border-r border-line bg-surface p-10 lg:flex">
        <Brand />
        <div>
          <div className="eyebrow mb-4">Component quality · genealogy · dispatch integrity</div>
          <h1 className="max-w-lg text-[40px] font-semibold leading-[1.08] tracking-tight">Every part ships with its <span className="text-accent">evidence</span>, or it doesn’t ship.</h1>
          <p className="mt-4 max-w-md text-[14px] text-muted">Deterministic policy gates from material heat to the loading dock. Vision results are evidence, never approval.</p>
          <div className="mt-10 flex items-center">
            {CHAIN.map((c, i) => (
              <div key={c} className="flex items-center">
                <div className="rise flex flex-col items-center gap-2" style={{ animationDelay: `${i * 70}ms` }}>
                  <span className="grid size-9 place-items-center rounded-full border border-line-strong bg-surface font-mono text-[11px] text-accent">{String(i + 1).padStart(2, '0')}</span>
                  <span className="font-mono text-[10.5px] text-muted">{c}</span>
                </div>
                {i < CHAIN.length - 1 && <span className="mx-1.5 mb-6 h-px w-7 bg-gradient-to-r from-accent/60 to-line-strong" />}
              </div>
            ))}
          </div>
        </div>
        <div className="text-[11.5px] text-muted">Hackathon prototype · fictional demo data · no OEM endorsement implied</div>
      </section>

      <section className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden"><Brand /></div>
          <h2 className="text-xl font-semibold">Sign in</h2>
          <p className="mt-1 text-[13px] text-muted">Use a development account. {API_MODE === 'mock' && <Honesty kind="mock" />}</p>
          <form className="mt-6 space-y-4" onSubmit={e => { e.preventDefault(); go() }}>
            <Field label="Username"><Input value={u} onChange={e => setU(e.target.value)} autoComplete="username" /></Field>
            <Field label="Password" hint="All dev accounts use autoq-dev"><Input type="password" value={p} onChange={e => setP(e.target.value)} autoComplete="current-password" /></Field>
            {err ? <ErrorBox error={err} /> : null}
            <Button variant="primary" className="w-full" loading={busy}>Sign in <ArrowRight size={15} /></Button>
          </form>
          <div className="mt-8">
            <div className="eyebrow mb-2">Quick roles</div>
            <div className="grid gap-1.5">
              {DEV_USERS.map(d => (
                <button key={d.username} onClick={() => go(d.username, 'autoq-dev')} className="flex items-center justify-between rounded-lg border border-line bg-surface px-3 py-2 text-left hover:border-accent">
                  <span><span className="block text-[13px] font-medium">{ROLE_LABEL[d.role]}</span><span className="block text-[11.5px] text-muted">{d.name} · {d.does}</span></span>
                  <span className="font-mono text-[11px] text-muted">{d.username}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
