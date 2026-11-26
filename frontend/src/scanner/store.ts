import { openDB, type IDBPDatabase } from 'idb'
import { chainHash, GENESIS } from '../lib/chain'
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
  const full = { ...ev, client_event_id, seq, prev_hash: prev, hash: '' } as QueuedScan
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
