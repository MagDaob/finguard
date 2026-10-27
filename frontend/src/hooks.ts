import { useEffect, useState } from 'react'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { accountSchema, recipientSchema, taskRowSchema, type Task } from './api'
import { pollInterval } from './state'
import { useSession } from './session'

export function useVisible() {
    const [visible, set] = useState(!document.hidden)
    useEffect(() => {
        const change = () => set(!document.hidden)
        document.addEventListener('visibilitychange', change)
        return () => document.removeEventListener('visibilitychange', change)
    }, [])
    return visible
}
export function useTask(ref?: string) {
    const { api, me } = useSession()
    const visible = useVisible()
    return useQuery({
        queryKey: [me?.subject_ref, 'task', ref],
        enabled: !!api && !!ref,
        queryFn: ({ signal }) => api!.task(ref!, signal),
        refetchInterval: (query) =>
            pollInterval(query.state.data?.status ?? '', query.state.fetchFailureCount, visible),
        refetchIntervalInBackground: false,
    })
}
export function useTasks() {
    const { api, me } = useSession()
    return useInfiniteQuery({
        queryKey: [me?.subject_ref, 'tasks'],
        enabled: !!api && !!me?.scopes.includes('tasks.read'),
        initialPageParam: '',
        queryFn: ({ pageParam, signal }) =>
            api!.get(
                '/v1/tasks?limit=25' +
                    (pageParam ? '&before=' + encodeURIComponent(pageParam) : ''),
                z.array(taskRowSchema),
                signal,
            ),
        getNextPageParam: (last) => (last.length === 25 ? last.at(-1)!.task_ref : undefined),
    })
}
export function useAccounts() {
    const { api, me } = useSession()
    return useQuery({
        queryKey: [me?.subject_ref, 'accounts'],
        enabled: !!api && !!me?.scopes.includes('accounts.read'),
        queryFn: ({ signal }) => api!.get('/v1/accounts', z.array(accountSchema), signal),
    })
}
export function useRecipients() {
    const { api, me } = useSession()
    return useQuery({
        queryKey: [me?.subject_ref, 'recipients'],
        enabled: !!api && !!me?.scopes.includes('accounts.read'),
        queryFn: ({ signal }) => api!.get('/v1/recipients', z.array(recipientSchema), signal),
    })
}
export function useRefresh() {
    const { me } = useSession(),
        q = useQueryClient()
    return async (task?: Task) => {
        await Promise.all([
            q.invalidateQueries({ queryKey: [me?.subject_ref, 'task', task?.task_ref] }),
            q.invalidateQueries({ queryKey: [me?.subject_ref, 'tasks'] }),
            q.invalidateQueries({ queryKey: [me?.subject_ref, 'accounts'] }),
            q.invalidateQueries({ queryKey: [me?.subject_ref, 'ledger'] }),
            q.invalidateQueries({ queryKey: [me?.subject_ref, 'services'] }),
            q.invalidateQueries({ queryKey: [me?.subject_ref, 'products'] }),
            q.invalidateQueries({ queryKey: [me?.subject_ref, 'positions'] }),
            q.invalidateQueries({ queryKey: [me?.subject_ref, 'delegations'] }),
            q.invalidateQueries({ queryKey: [me?.subject_ref, 'preview'] }),
        ])
    }
}
