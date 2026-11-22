import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from '@tanstack/react-query'
import { api } from './client'

export const useGet = <T,>(path: string | null, opts?: Partial<UseQueryOptions<T>>) =>
  useQuery<T>({ queryKey: [path], queryFn: () => api.get<T>(path!), enabled: !!path, ...opts })

/** Mutations invalidate everything: the policy engine's effects ripple across entities. */
export function useAct<TBody = any, TRes = any>(fn: (b: TBody) => Promise<TRes>) {
  const qc = useQueryClient()
  return useMutation<TRes, any, TBody>({ mutationFn: fn, onSuccess: () => qc.invalidateQueries() })
}
