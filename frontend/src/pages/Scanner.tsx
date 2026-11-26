import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, CircleAlert, CloudOff, Download, Link2, QrCode, RefreshCw, Settings2, ShieldCheck, Wifi, WifiOff, XOctagon, ListOrdered, Hourglass, Monitor } from 'lucide-react'
import { api, API_MODE } from '../api/client'
import { useAuth } from '../auth/auth'
import type { GateCheck, Package, Reason, ScanDecision, Shipment } from '../api/types'
import { Brand } from '../layout/AppShell'
import { Button, Honesty, Mono, Pill, Status, cx, ago, fmtTime } from '../components/ui'
import { GateChecklist, Reasons } from '../components/domain'
import { QrCamera } from '../scanner/QrCamera'
import { decideLocally, reconciledStatus, snapshotFor, type QueuedScan, type SnapPayload, type StoredSnapshot, type SyncResult } from '../scanner/offline'
import { appendScan, deleteSnapshot, deviceInfo, getPref, getSnapshot, listQueue, putSnapshot, setPref, updateScan } from '../scanner/store'
import { decodeTokenUnsafe, verifyToken } from '../lib/token'
import { verifyChain } from '../lib/chain'

type ShipView = Shipment & { expected_units: number; loaded_units: number }
type Tab = 'scan' | 'queue' | 'setup'
const SYNC_BATCH = 200   // well under the API's 500-event limit
interface Shown { kind: 'ONLINE' | 'OFFLINE'; decision: string; label: string | null; reasons: Reason[]; checks: GateCheck[]; at: string; replay?: boolean; event: string }

/* Network state: real connectivity AND an explicit "simulate network loss" switch (needed in mock mode, where the backend lives in this tab). */
function useNet() {
  const [real, setReal] = useState(navigator.onLine)
  const [forced, setForcedS] = useState(() => { try { return localStorage.getItem('autoq-force-offline') === '1' } catch { return false } })
  useEffect(() => { const on = () => setReal(true), off = () => setReal(false); addEventListener('online', on); addEventListener('offline', off); return () => { removeEventListener('online', on); removeEventListener('offline', off) } }, [])
  const setForced = (v: boolean) => { setForcedS(v); try { v ? localStorage.setItem('autoq-force-offline', '1') : localStorage.removeItem('autoq-force-offline') } catch { /* ignore */ } }
  return { online: real && !forced, real, forced, setForced }
}

const buzz = (ok: boolean) => { try { navigator.vibrate?.(ok ? 40 : [60, 60, 60]) } catch { /* ignore */ } }

function ResultCard({ r, onDismiss }: { r: Shown; onDismiss: () => void }) {
  const tone = r.decision === 'ACCEPTED' ? 'var(--pass)' : r.decision === 'PROVISIONAL_ACCEPT' ? 'var(--prov)' : 'var(--reject)'
  const title = { ACCEPTED: 'LOAD ACCEPTED', BLOCKED: 'LOAD BLOCKED', PROVISIONAL_ACCEPT: 'PROVISIONAL · LOAD', LOCAL_BLOCK: 'DO NOT LOAD' }[r.decision] ?? r.decision
  const [open, setOpen] = useState(false)
  return (
    <div className="rise overflow-hidden rounded-2xl border" style={{ borderColor: `color-mix(in srgb, ${tone} 45%, transparent)`, background: `color-mix(in srgb, ${tone} 10%, var(--surface))` }}>
      <button onClick={onDismiss} className="flex w-full items-center gap-3 p-4 text-left">
        {r.decision === 'ACCEPTED' ? <CheckCircle2 size={34} style={{ color: tone }} /> : r.decision === 'PROVISIONAL_ACCEPT' ? <Hourglass size={34} style={{ color: tone }} /> : <XOctagon size={34} style={{ color: tone }} />}
        <div className="min-w-0 flex-1">
          <div className="num text-[22px] font-semibold leading-none tracking-tight" style={{ color: tone }}>{title}</div>
          <div className="mt-1 truncate text-[12.5px] text-ink-2"><Mono>{r.label ?? 'unidentified'}</Mono> · {fmtTime(r.at)} {r.replay && '· replay'}</div>
        </div>
        <span className="text-[11px] text-muted">tap to scan next</span>
      </button>
      <div className="space-y-3 border-t border-line/60 px-4 py-3">
        {r.kind === 'OFFLINE' && <div className="flex items-start gap-2 rounded-lg bg-prov/10 px-3 py-2 text-[12px] text-ink-2"><CloudOff size={14} className="mt-0.5 shrink-0 text-prov" /><span><b>Not authoritative.</b> Decided on this device from a signed snapshot. Seal state, newer holds and other devices’ scans are checked when you reconnect — the crate may have to come off.</span></div>}
        <Reasons reasons={r.reasons} />
        <button onClick={() => setOpen(o => !o)} className="text-[12px] text-accent">{open ? 'Hide' : 'Show'} gate checks ({r.checks.length})</button>
        {open && <GateChecklist checks={r.checks} />}
        <div className="font-mono text-[10.5px] text-muted">event {r.event}</div>
      </div>
    </div>
  )
}

export default function Scanner() {
  const { user, can, login, unverified } = useAuth()
  const net = useNet()
  const [tab, setTab] = useState<Tab>('scan')
  const [ships, setShips] = useState<ShipView[] | null>(null)
  const [shipId, setShipId] = useState<string>('')
  const [snap, setSnap] = useState<StoredSnapshot | undefined>()
  const [queue, setQueue] = useState<QueuedScan[]>([])
  const [dev, setDev] = useState<{ device_id: string; seq: number; head: string } | null>(null)
  const [shown, setShown] = useState<Shown | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)
  const lastText = useRef<{ t: string; at: number }>({ t: '', at: 0 })
  const [, tickNow] = useState(0)
  useEffect(() => { const i = setInterval(() => tickNow(n => n + 1), 15_000); return () => clearInterval(i) }, [])

  const refreshLocal = useCallback(async () => { setQueue(await listQueue()); setDev(await deviceInfo()) }, [])
  useEffect(() => { refreshLocal(); getPref<string>('shipment').then(s => s && setShipId(s)) }, [refreshLocal])
  useEffect(() => { if (shipId) { setPref('shipment', shipId); getSnapshot(shipId).then(setSnap) } }, [shipId])
  const loadShips = useCallback(async () => {
    if (!net.online) return
    try { const r = await api.get<{ items: ShipView[] }>('/shipments'); setShips(r.items); setShipId(id => id || r.items.find(s => s.status === 'OPEN')?.id || '') } catch { /* offline */ }
  }, [net.online])
  useEffect(() => { loadShips() }, [loadShips])

  const ship = ships?.find(s => s.id === shipId)
  const pending = queue.filter(q => !q.synced_at)
  const provisional = queue.filter(q => q.shipment_id === shipId && q.local_decision === 'PROVISIONAL_ACCEPT' && q.status === 'PENDING')
  const conflicts = queue.filter(q => q.status === 'CONFLICT')
  const chain = useMemo(() => verifyChain(queue), [queue])
  const snapAgeMin = snap ? (Date.now() / 1000 - snap.payload.iat) / 60 : null
  const snapLeftMin = snap ? (snap.payload.exp - Date.now() / 1000) / 60 : null

  /* ── download & verify a signed offline snapshot ── */
  const prepareOffline = async (quiet = false) => {
    if (!shipId) return
    if (!quiet) { setBusy('snapshot'); setMsg(null) }
    try {
      // Reporting the local queue lets the server count an empty-queue refresh as a check-in (manifest close waits for devices that haven't).
      const [info, q] = await Promise.all([deviceInfo(), listQueue()])
      const pendingNow = q.filter(x => !x.synced_at).length
      const [{ token }, { keys }, pk] = await Promise.all([
        api.get<{ token: string }>(`/offline/snapshot?shipment_id=${shipId}&device_id=${encodeURIComponent(info.device_id)}&local_seq=${info.seq}&pending=${pendingNow}`),
        api.get<{ keys: { kid: string; public_key: string; status: string }[] }>('/keys'),
        api.get<{ items: Package[] }>('/packages'),
      ])
      const v = await verifyToken(token, keys)
      if (!v.ok || v.payload.typ !== 'SNAP') throw new Error(`Snapshot signature check failed (${v.ok ? 'wrong type' : v.code})`)
      const payload = v.payload as SnapPayload
      const demo_labels = pk.items.filter(p => p.token && ['SEALED', 'SEAL_REVIEW', 'LOADED'].includes(p.state)).map(p => ({ id: p.id, label: p.label, token: p.token! }))
      const s: StoredSnapshot = { token, payload, verified_at: new Date().toISOString(), shipment_code: ships?.find(x => x.id === shipId)?.code ?? shipId, demo_labels }
      await putSnapshot(s); setSnap(s)
      if (!quiet) setMsg({ tone: 'ok', text: `Snapshot verified (kid ${v.kid}) · valid ${Math.round((payload.exp - payload.iat) / 60)} min` })
    } catch (e: any) { if (!quiet) setMsg({ tone: 'err', text: e?.message ?? 'Could not prepare offline mode' }) }
    finally { if (!quiet) setBusy(null) }
  }

  /* ── one scan, online or offline ── */
  const handle = useCallback(async (token: string) => {
    if (!shipId || busy === 'scan') return
    setBusy('scan'); setMsg(null)
    const now = new Date().toISOString()
    try {
      if (net.online) {
        const claimed: any = decodeTokenUnsafe(token)
        const ev = await appendScan({ token, shipment_id: shipId, scanned_at: now, local_decision: 'ONLINE', local_reasons: [], local_checks: [], package_id: null, package_label: claimed?.label ?? null, units: Array.isArray(claimed?.items) ? claimed.items.length : 0, status: 'PENDING' })
        try {
          const d = await api.post<ScanDecision>(`/shipments/${shipId}/scans`, { token, client_event_id: ev.client_event_id, device_id: dev?.device_id })
          await updateScan({ ...ev, status: d.decision, package_id: d.package_id, package_label: d.package_label ?? ev.package_label, server_reasons: d.reasons, server_checks: d.checks })
          setShown({ kind: 'ONLINE', decision: d.decision, label: d.package_label ?? null, reasons: d.reasons, checks: d.checks, at: d.decided_at, replay: d.replay, event: ev.client_event_id })
          buzz(d.decision === 'ACCEPTED')
          loadShips()
        } catch (e: any) {
          await updateScan({ ...ev, note: `Request failed (${e?.code ?? 'network'}) — treat as NOT loaded` })
          setMsg({ tone: 'err', text: e?.status === 403 ? 'ROLE_FORBIDDEN — dispatcher role required' : `Server unreachable: ${e?.message ?? 'network error'}. Not loaded.` })
        }
      } else {
        if (!snap) { setMsg({ tone: 'err', text: 'No offline snapshot for this shipment. Reconnect and prepare offline mode first.' }); return }
        const r = await decideLocally(token, snap.payload, await listQueue(), Date.now() / 1000, Date.parse(snap.verified_at))
        const ev = await appendScan({ token, shipment_id: shipId, scanned_at: now, local_decision: r.decision, local_reasons: r.reasons, local_checks: r.checks, package_id: r.package_id, package_label: r.package_label, units: r.units, status: 'PENDING', snapshot_id: snap.payload.id })
        setShown({ kind: 'OFFLINE', decision: r.decision, label: r.package_label, reasons: r.reasons, checks: r.checks, at: now, event: ev.client_event_id })
        buzz(r.decision === 'PROVISIONAL_ACCEPT')
      }
    } finally { await refreshLocal(); setBusy(null) }
  }, [shipId, net.online, snap, dev, busy, refreshLocal, loadShips])

  const onDecode = (text: string) => {
    if (shown || busy) return
    const now = Date.now()
    if (text === lastText.current.t && now - lastText.current.at < 4000) return
    lastText.current = { t: text, at: now }
    if (!text.startsWith('AQ1.')) { setMsg({ tone: 'err', text: 'Not an AutoQ label (expected AQ1.… token)' }); return }
    handle(text)
  }

  /* ── sync the device queue (all unsynced events, in chain order) ── */
  const sync = async () => {
    setBusy('sync'); setMsg(null)
    let done = 0, conflicts = 0
    const decided = new Set<string>()
    try {
      const unsynced = (await listQueue()).filter(q => !q.synced_at).sort((a, b) => a.seq - b.seq)
      if (!unsynced.length) { setMsg({ tone: 'ok', text: 'Nothing to sync' }); return }
      const stored = (await Promise.all([...new Set(unsynced.map(e => e.shipment_id))].map(getSnapshot))).filter((x): x is StoredSnapshot => !!x)
      // Bounded batches in chain order (the API takes ≤ 500). Each batch continues from where the last one ended; a failed
      // batch stops the run, and whatever the server already committed comes back as DUPLICATE on the next sync.
      for (let i = 0; i < unsynced.length; i += SYNC_BATCH) {
        const batch = unsynced.slice(i, i + SYNC_BATCH)
        const res = await api.post<{ results: (SyncResult & { scan?: ScanDecision })[]; chain: { ok: boolean; broken_at?: number } }>('/sync/scans', {
          device_id: dev?.device_id,
          events: batch.map(e => ({ client_event_id: e.client_event_id, seq: e.seq, prev_hash: e.prev_hash, hash: e.hash, token: e.token, shipment_id: e.shipment_id,
            scanned_at: e.scanned_at, local_decision: e.local_decision, local_reasons: e.local_reasons, snapshot_id: snapshotFor(e, stored) })),
        })
        if (!res.chain.ok) { setMsg({ tone: 'err', text: `Server rejected the batch: device chain broken at seq ${res.chain.broken_at}. Escalate — events were altered after capture.` }); return }
        const at = new Date().toISOString()
        for (const r of res.results) {
          const ev = batch.find(e => e.client_event_id === r.client_event_id); if (!ev) continue
          const status = reconciledStatus(ev, r)
          // A lost online response is recovered here: the server's original decision and the package it identified.
          await updateScan({ ...ev, status, synced_at: at, note: r.note || ev.note, server_reasons: r.scan?.reasons ?? ev.server_reasons, server_checks: r.scan?.checks ?? ev.server_checks,
            package_id: r.scan?.package_id ?? ev.package_id, package_label: r.scan?.package_label ?? ev.package_label })
          decided.add(r.client_event_id)
          if (status === 'CONFLICT') conflicts++
        }
        done += res.results.length
      }
      setMsg({ tone: conflicts ? 'err' : 'ok', text: `Synced ${done} event(s)${conflicts ? ` · ${conflicts} conflict(s) need physical action` : ''}` })
      if (conflicts) setTab('queue')
      loadShips(); await prepareOffline(true)
    } catch (e: any) { setMsg({ tone: 'err', text: `${done ? `Synced ${done} event(s), then: ` : ''}${e?.message ?? 'Sync failed'}` }) }
    finally {
      // The offline card on the Scan tab was the device's guess; once the server has decided that event, the Queue holds the truth.
      setShown(cur => (cur && decided.has(cur.event) ? null : cur))
      await refreshLocal(); setBusy(null)
    }
  }

  /* ── end the offline session: hand the authorization back so the manifest can close ── */
  const endOffline = async () => {
    if (!snap || !shipId) return
    setBusy('settle'); setMsg(null)
    try {
      const [info, q] = await Promise.all([deviceInfo(), listQueue()])
      await api.post(`/offline/snapshots/${snap.payload.id}/settle`, { device_id: info.device_id, local_seq: info.seq, pending: q.filter(x => !x.synced_at).length })
      await deleteSnapshot(shipId); setSnap(undefined)
      setMsg({ tone: 'ok', text: 'Offline session ended. This phone can no longer load offline for this shipment.' })
    } catch (e: any) {
      setMsg({ tone: 'err', text: e?.code === 'SETTLEMENT_PENDING_EVENTS' ? 'Sync every queued scan first, then end the session.' : e?.message ?? 'Could not end the offline session' })
    } finally { setBusy(null) }
  }

  if (!user || !can('dispatcher')) return (
    <div className="grid min-h-full place-items-center p-6">
      <div className="w-full max-w-sm space-y-4 text-center">
        <div className="flex justify-center"><Brand /></div>
        <p className="text-[13px] text-muted">The dock scanner is for the <b className="text-ink">Dispatcher</b> role. Scans by other roles are refused by the server.</p>
        <Button variant="primary" className="w-full" onClick={() => login('disp.kiran', 'autoq-dev')}>Continue as Kiran Das (dev)</Button>
        <Link to="/" className="block text-[12.5px] text-accent">Back to control room</Link>
      </div>
    </div>
  )

  const netPill = net.online
    ? <Pill tone="pass"><Wifi size={11} /> ONLINE</Pill>
    : <Pill tone="prov"><WifiOff size={11} /> OFFLINE{net.forced ? ' · SIMULATED' : ''}</Pill>

  return (
    <div className="mx-auto flex min-h-full max-w-md flex-col bg-bg sm:border-x sm:border-line">
      <header className="sticky top-0 z-20 flex items-center gap-2 border-b border-line bg-surface/90 px-4 py-3 backdrop-blur">
        <Link to="/" aria-label="Control room" className="text-muted"><ArrowLeft size={18} /></Link>
        <Brand compact />
        <div className="min-w-0 flex-1 leading-tight"><div className="truncate text-[13.5px] font-semibold">{ship?.code ?? snap?.shipment_code ?? 'Dock scanner'}</div><div className="truncate text-[11px] text-muted">{ship ? `${ship.dock} · ${ship.vehicle_no}` : user.display_name}</div></div>
        {netPill}
        {unverified && <span title={`Signed in from this device's cache — the server could not confirm the session (${unverified}). It will re-check when signal returns.`}><Honesty kind="provisional">ID UNVERIFIED</Honesty></span>}
      </header>

      {(conflicts.length > 0 || (!net.online && snapLeftMin !== null && snapLeftMin < 30)) && (
        <div className="space-y-1 border-b border-line bg-surface px-4 py-2 text-[12px]">
          {conflicts.length > 0 && <button onClick={() => setTab('queue')} className="flex items-center gap-1.5 text-reject"><CircleAlert size={13} /> {conflicts.length} conflict(s) — physical action required</button>}
          {!net.online && snapLeftMin !== null && snapLeftMin < 30 && <div className="flex items-center gap-1.5 text-hold"><Hourglass size={13} /> Snapshot {snapLeftMin > 0 ? `expires in ${Math.round(snapLeftMin)} min` : 'expired — all scans will block'}</div>}
        </div>
      )}

      <main className="flex-1 space-y-4 p-4 pb-28">
        {msg && <div className={cx('rise rounded-xl border px-3 py-2 text-[12.5px]', msg.tone === 'ok' ? 'border-pass/40 text-pass' : 'border-reject/40 text-reject')}>{msg.text}</div>}

        {tab === 'scan' && <>
          {!shipId && <div className="rounded-xl border border-line bg-surface p-4 text-[13px] text-muted">Pick a shipment in <button className="text-accent" onClick={() => setTab('setup')}>Setup</button>.</div>}
          {shipId && <>
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded-xl border border-line bg-surface py-2"><div className="num text-[18px]">{ship ? `${ship.loaded_units}/${ship.expected_units}` : '—'}</div><div className="text-[10.5px] text-muted">server-confirmed</div></div>
              <div className="rounded-xl border border-line bg-surface py-2"><div className="num text-[18px] text-prov">{provisional.reduce((n, q) => n + q.units, 0)}</div><div className="text-[10.5px] text-muted">provisional units</div></div>
              <div className="rounded-xl border border-line bg-surface py-2"><div className="num text-[18px]">{pending.length}</div><div className="text-[10.5px] text-muted">unsynced events</div></div>
            </div>
            {shown ? <ResultCard r={shown} onDismiss={() => setShown(null)} /> : <QrCamera onDecode={onDecode} paused={!!busy} />}
            {!net.online && !snap && <div className="rounded-xl border border-hold/40 px-3 py-2 text-[12.5px] text-hold">Offline without a snapshot: nothing can be verified on this device.</div>}
            <div className="rounded-xl border border-line bg-surface">
              <div className="flex items-center justify-between border-b border-line px-3 py-2"><span className="eyebrow">No label handy?</span><Honesty kind="synthetic">SIMULATED SCAN</Honesty></div>
              <div className="flex flex-wrap gap-1.5 p-3">
                {(snap?.demo_labels ?? []).map(l => <button key={l.id} disabled={!!busy} onClick={() => { setShown(null); handle(l.token) }} className="rounded-lg border border-line px-2.5 py-1.5 font-mono text-[12px] hover:border-accent disabled:opacity-40">{l.label}</button>)}
                {!snap && <span className="text-[12px] text-muted">Prepare offline mode in Setup to cache demo labels — or point the camera at a package QR shown in the control room.</span>}
              </div>
            </div>
          </>}
        </>}

        {tab === 'queue' && <>
          <div className="flex items-center gap-2">
            <Button variant="primary" className="flex-1" disabled={!net.online || !pending.length} loading={busy === 'sync'} onClick={sync}><RefreshCw size={14} /> Sync {pending.length} event(s)</Button>
          </div>
          <div className="flex items-center gap-2 rounded-xl border border-line bg-surface px-3 py-2 text-[12px]">
            <Link2 size={13} className={chain.ok ? 'text-pass' : 'text-reject'} />
            {chain.ok ? <>Device chain intact · {queue.length} events · head <Mono>{chain.head.slice(0, 10)}…</Mono></> : <span className="text-reject">Local chain broken at seq {chain.broken_at}</span>}
          </div>
          <ul className="space-y-2">
            {queue.map(q => {
              const conflict = q.status === 'CONFLICT'
              return (
                <li key={q.client_event_id} className={cx('rounded-xl border bg-surface p-3', conflict ? 'border-reject/50' : 'border-line')}>
                  <div className="flex items-center gap-2">
                    <span className="num w-8 text-[11px] text-muted">#{q.seq}</span>
                    <Mono className="font-semibold">{q.package_label ?? '?'}</Mono>
                    {q.local_decision !== 'ONLINE' && <Status value={q.local_decision === 'PROVISIONAL_ACCEPT' ? 'PROVISIONAL_OFFLINE' : 'BLOCKED'} />}
                    <span className="ml-auto">{q.status === 'PENDING' ? <Pill tone="prov">UNSYNCED</Pill> : <Status value={q.status} />}</span>
                  </div>
                  <div className="mt-1 text-[11px] text-muted">{q.local_decision === 'ONLINE' ? 'online scan' : 'offline scan'} · {ago(q.scanned_at)}{q.synced_at && ` · synced ${ago(q.synced_at)}`}</div>
                  {conflict && <div className="mt-2 rounded-lg bg-reject/10 px-2.5 py-1.5 text-[12px] text-reject"><b>Physical action:</b> {q.note}</div>}
                  {!conflict && q.note && <div className="mt-1 text-[11.5px] text-muted">{q.note}</div>}
                  {(q.server_reasons?.length ?? 0) > 0 && <div className="mt-2"><Reasons reasons={q.server_reasons!} /></div>}
                  {q.status === 'PENDING' && q.local_reasons.length > 0 && <div className="mt-2"><Reasons reasons={q.local_reasons} /></div>}
                </li>
              )
            })}
            {!queue.length && <li className="rounded-xl border border-dashed border-line p-6 text-center text-[13px] text-muted"><ListOrdered className="mx-auto mb-2" size={18} />No events on this device yet.</li>}
          </ul>
        </>}

        {tab === 'setup' && <>
          <section className="rounded-xl border border-line bg-surface">
            <div className="border-b border-line px-3 py-2 eyebrow">Shipment</div>
            <div className="space-y-1 p-2">
              {(ships ?? []).filter(s => s.status === 'OPEN').map(s => (
                <button key={s.id} onClick={() => setShipId(s.id)} className={cx('flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left', s.id === shipId ? 'bg-accent-soft' : 'hover:bg-surface-2')}>
                  <Mono className="font-semibold">{s.code}</Mono><span className="text-[12px] text-muted">{s.dock} · {s.vehicle_no}</span><span className="num ml-auto text-[11.5px]">{s.loaded_units}/{s.expected_units}</span>
                </button>
              ))}
              {!ships && <div className="p-2 text-[12.5px] text-muted">{net.online ? 'Loading…' : 'Offline — showing cached snapshot only.'}</div>}
            </div>
          </section>

          <section className="rounded-xl border border-line bg-surface">
            <div className="flex items-center justify-between border-b border-line px-3 py-2"><span className="eyebrow">Offline readiness</span>{snap && <ShieldCheck size={14} className="text-pass" />}</div>
            <div className="space-y-3 p-3 text-[12.5px]">
              {snap ? (
                <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5">
                  <dt className="text-muted">Snapshot</dt><dd><Mono>{snap.payload.id}</Mono></dd>
                  <dt className="text-muted">Issued</dt><dd>{snapAgeMin !== null ? `${Math.round(snapAgeMin)} min ago` : '—'}</dd>
                  <dt className="text-muted">Expires</dt><dd className={cx(snapLeftMin! < 0 && 'text-reject')}>{snapLeftMin! > 0 ? `in ${Math.round(snapLeftMin!)} min` : 'expired'}</dd>
                  <dt className="text-muted">Allowed / held</dt><dd className="num">{snap.payload.allowed_packages.length} / {snap.payload.held.length} pkgs</dd>
                  <dt className="text-muted">Trusted keys</dt><dd><Mono>{snap.payload.keys.map(k => k.kid).join(', ')}</Mono></dd>
                </dl>
              ) : <div className="text-muted">No snapshot. Offline scans need a signed, short-lived manifest snapshot downloaded while online.</div>}
              <Button className="w-full" disabled={!net.online || !shipId} loading={busy === 'snapshot'} onClick={() => prepareOffline()}><Download size={14} /> {snap ? 'Refresh' : 'Download'} signed snapshot</Button>
              {snap && <Button className="w-full" variant="ghost" disabled={!net.online || !!busy || pending.length > 0} loading={busy === 'settle'} onClick={endOffline}><CloudOff size={14} /> End offline session</Button>}
              {snap && <div className="text-[11px] leading-relaxed text-muted">The manifest cannot close while this phone holds an offline authorization. Sync, then end the session{pending.length > 0 ? ` (${pending.length} scan(s) still to sync)` : ''}.</div>}
              <div className="text-[11px] leading-relaxed text-muted">Offline, the device checks signature, key, order, snapshot holds, local duplicates and quantity. It cannot know newer holds, other devices’ scans or seal state — so every offline decision is <b className="text-prov">provisional</b> and never counts as loaded until the server accepts it on sync.</div>
            </div>
          </section>

          <section className="rounded-xl border border-line bg-surface p-3">
            <label className="flex items-center gap-3">
              <input type="checkbox" checked={net.forced} onChange={e => net.setForced(e.target.checked)} className="size-5 accent-[var(--prov)]" />
              <span className="flex-1"><span className="block text-[13px] font-medium">Simulate network loss</span><span className="block text-[11.5px] text-muted">{API_MODE === 'mock' ? 'Mock mode: the backend runs in this browser, so connectivity loss is simulated with this switch.' : 'Stops all API calls from the scanner, as if the dock had no signal.'}</span></span>
            </label>
            {!net.real && <div className="mt-2 text-[11.5px] text-hold">The device itself reports no network.</div>}
          </section>

          <section className="rounded-xl border border-line bg-surface p-3 text-[12px]">
            <div className="eyebrow mb-2">Device</div>
            <dl className="grid grid-cols-2 gap-y-1">
              <dt className="text-muted">Device id</dt><dd><Mono>{dev?.device_id}</Mono></dd>
              <dt className="text-muted">Sequence</dt><dd className="num">{dev?.seq}</dd>
              <dt className="text-muted">Chain head</dt><dd><Mono>{dev?.head.slice(0, 12)}…</Mono></dd>
              <dt className="text-muted">Operator</dt><dd>{user.display_name}</dd>
            </dl>
            <Link to="/dispatch" className="mt-3 flex items-center gap-1.5 text-accent"><Monitor size={13} /> Open control room dispatch view</Link>
          </section>
        </>}
      </main>

      <nav className="fixed inset-x-0 bottom-0 z-20 mx-auto grid max-w-md grid-cols-3 border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur sm:border-x">
        {([['scan', 'Scan', <QrCode size={20} />], ['queue', 'Queue', <ListOrdered size={20} />], ['setup', 'Setup', <Settings2 size={20} />]] as const).map(([k, label, icon]) => (
          <button key={k} onClick={() => setTab(k)} className={cx('relative flex flex-col items-center gap-0.5 py-2.5 text-[11px]', tab === k ? 'text-accent' : 'text-muted')}>
            {icon}{label}
            {k === 'queue' && pending.length > 0 && <span className="num absolute right-[30%] top-1.5 rounded-full bg-prov px-1.5 text-[10px] text-white">{pending.length}</span>}
          </button>
        ))}
      </nav>
    </div>
  )
}
