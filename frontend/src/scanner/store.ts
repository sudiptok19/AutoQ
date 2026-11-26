import { openDB, type IDBPDatabase } from 'idb'
import { chainHash, GENESIS, SUPPORTED_CHAIN_VERSIONS } from '../lib/chain'
import type { QueuedScan, StoredSnapshot } from './offline'

let dbp: Promise<IDBPDatabase> | null = null
const db = () => (dbp ??= openDB('autoq-scanner', 1, {
  upgrade(d) { d.createObjectStore('meta'); d.createObjectStore('queue', { keyPath: 'client_event_id' }).createIndex('seq', 'seq', { unique: true }) },
}))

export async function deviceInfo(): Promise<{ device_id: string; seq: number; head: string }> {
  const d = await db()
  let id = await d.get('meta', 'device_id')
  if (!id) { id = 'dev-' + crypto.getRandomValues(new Uint8Array(3)).reduce((s, b) => s + b.toString(16).padStart(2, '0'), ''); await d.put('meta', id, 'device_id') }
  return { device_id: id, seq: (await d.get('meta', 'seq')) ?? 0, head: (await d.get('meta', 'head')) ?? GENESIS }
}

/** Append atomically: seq, prev_hash and hash are assigned inside one IndexedDB transaction. */
export async function appendScan(ev: Omit<QueuedScan, 'client_event_id' | 'seq' | 'prev_hash' | 'hash'>): Promise<QueuedScan> {
  const { device_id } = await deviceInfo()
  const d = await db()
  const tx = d.transaction(['meta', 'queue'], 'readwrite')
  const meta = tx.objectStore('meta')
  const seq = ((await meta.get('seq')) ?? 0) + 1
  const prev = (await meta.get('head')) ?? GENESIS
  const client_event_id = `${device_id}:${String(seq).padStart(6, '0')}`
  // Writer version is read inside the same transaction as seq/head, so every tab appends under the device's current
  // writer (never lower than one it has used). v2 hashes the capture snapshot: explicit null for ONLINE.
  const version: number = (await meta.get('chain_writer')) ?? 1
  const v2 = version === 2 ? { chain_version: 2, snapshot_id: ev.local_decision === 'ONLINE' ? null : (ev.snapshot_id ?? null) } : {}
  const full = { ...ev, ...v2, client_event_id, seq, prev_hash: prev, hash: '' } as QueuedScan
  full.hash = chainHash(prev, full)
  await meta.put(seq, 'seq'); await meta.put(full.hash, 'head')
  await tx.objectStore('queue').put(full)
  await tx.done
  return full
}

export const listQueue = async (): Promise<QueuedScan[]> => (await (await db()).getAllFromIndex('queue', 'seq')).reverse()
export const updateScan = async (s: QueuedScan) => { await (await db()).put('queue', s) }
export const getSnapshot = async (shipment: string): Promise<StoredSnapshot | undefined> => (await db()).get('meta', `snap:${shipment}`)
export const putSnapshot = async (s: StoredSnapshot) => { await (await db()).put('meta', s, `snap:${s.payload.shipment}`) }
export const deleteSnapshot = async (shipment: string) => { await (await db()).delete('meta', `snap:${shipment}`) }
export const getPref = async <T,>(k: string): Promise<T | undefined> => (await db()).get('meta', `pref:${k}`)
export const setPref = async (k: string, v: unknown) => { await (await db()).put('meta', v, `pref:${k}`) }

export interface ChainPolicy { writer_version: number; min_version: number; supported?: number[] }
/** Adopt the server's writer version (only upward, and only versions this build supports); report whether this build
 *  is below the server's floor, in which case it must stop taking offline decisions until it is updated. */
export async function applyChainPolicy(p: ChainPolicy): Promise<{ writer: number; outdated: boolean }> {
  const d = await db()
  const tx = d.transaction('meta', 'readwrite')
  const current: number = (await tx.store.get('chain_writer')) ?? 1
  const best = Math.max(...SUPPORTED_CHAIN_VERSIONS.filter(v => v <= p.writer_version), 1)
  const writer = Math.max(current, best)
  if (writer !== current) await tx.store.put(writer, 'chain_writer')
  await tx.store.put(p, 'chain_policy')
  await tx.done
  return { writer, outdated: p.min_version > Math.max(...SUPPORTED_CHAIN_VERSIONS) || writer < p.min_version }
}
export const chainPolicy = async (): Promise<ChainPolicy | undefined> => (await db()).get('meta', 'chain_policy')
export const chainWriter = async (): Promise<number> => ((await (await db()).get('meta', 'chain_writer')) ?? 1)
