import { Link } from 'react-router-dom'
import { ArrowLeft, Smartphone } from 'lucide-react'
import { Brand } from '../layout/AppShell'
import { Card } from '../components/ui'

/** Phone scanner PWA (offline queue, local Ed25519 verify, sync) — next milestone. */
export default function Scanner() {
  return (
    <div className="grid min-h-full place-items-center p-6">
      <Card className="max-w-sm p-6 text-center">
        <div className="mb-4 flex justify-center"><Brand /></div>
        <Smartphone className="mx-auto text-accent" />
        <h1 className="mt-3 text-lg font-semibold">Dock scanner</h1>
        <p className="mt-1 text-[13px] text-muted">Offline-capable phone scanner with camera QR, local signature checks and provisional queue is the next milestone. Use the Dispatch gate in the control room for now.</p>
        <Link to="/dispatch" className="mt-4 inline-flex items-center gap-1.5 text-[13px] text-accent hover:underline"><ArrowLeft size={14} /> Dispatch gate</Link>
      </Card>
    </div>
  )
}
