import { createContext, useContext, useRef, useState, type ReactNode } from 'react'
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { Api, ApiError, meSchema, type Me } from './api'

const SessionContext = createContext<{
    api: Api | null
    me: Me | null
    epoch: number
    login: (token: string) => Promise<void>
    logout: () => void
}>({ api: null, me: null, epoch: 0, login: async () => {}, logout: () => {} })
export function Session({ children }: { children: ReactNode }) {
    const query = useQueryClient(),
        [state, setState] = useState<{ api: Api; me: Me } | null>(null),
        [epoch, setEpoch] = useState(0),
        generation = useRef(0)
    const logout = () => {
        generation.current++
        void query.cancelQueries()
        query.clear()
        setState(null)
        setEpoch((x) => x + 1)
    }
    const login = async (token: string) => {
        const id = ++generation.current
        void query.cancelQueries()
        query.clear()
        setState(null)
        setEpoch((x) => x + 1)
        const api = new Api(
            token,
            () => {
                if (id === generation.current) logout()
            },
            () => id === generation.current,
        )
        const me = await api.get('/v1/me', meSchema)
        if (id === generation.current) setState({ api, me })
    }
    return (
        <SessionContext.Provider
            value={{ api: state?.api ?? null, me: state?.me ?? null, epoch, login, logout }}
        >
            {children}
        </SessionContext.Provider>
    )
}
export const useSession = () => useContext(SessionContext)
export const makeQueryClient = () =>
    new QueryClient({
        defaultOptions: {
            queries: {
                retry: (count, error) =>
                    !(
                        error instanceof ApiError &&
                        [401, 403, 404, 409, 422].includes(error.status)
                    ) && count < 3,
                retryDelay: (attempt) => Math.min(30000, 2000 * 2 ** attempt),
                refetchOnWindowFocus: true,
            },
            mutations: { retry: false },
        },
    })
export function Providers({ children }: { children: ReactNode }) {
    const [client] = useState(makeQueryClient)
    return (
        <QueryClientProvider client={client}>
            <Session>{children}</Session>
        </QueryClientProvider>
    )
}
