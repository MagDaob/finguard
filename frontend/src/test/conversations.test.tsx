import { useEffect } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { it, expect, vi, afterEach } from 'vitest'
import { Providers, useSession } from '../session'
import { ConversationRoute } from '../conversations'
function Login() {
    const { login } = useSession()
    useEffect(() => {
        void login('fixture')
    }, [])
    return null
}
function mount(path = '/tasks') {
    render(
        <MemoryRouter initialEntries={[path]}>
            <Providers>
                <Login />
                <Routes>
                    <Route path="/tasks" element={<ConversationRoute />} />
                    <Route path="/tasks/chat/:conversationRef" element={<ConversationRoute />} />
                </Routes>
            </Providers>
        </MemoryRouter>,
    )
}
afterEach(() => vi.unstubAllGlobals())
it('reuses the same message and request identity after a lost response', async () => {
    const writes: string[] = []
    vi.stubGlobal(
        'fetch',
        vi.fn(async (input: string, init?: RequestInit) => {
            const path = new URL(input, 'http://localhost').pathname
            if (init?.method === 'POST') {
                writes.push(String(init.body))
                if (writes.length === 1) throw new TypeError('lost response')
                return new Response(JSON.stringify({ conversation_ref: 'chat-fixture', turns: [] }))
            }
            return new Response(
                JSON.stringify(
                    path === '/v1/me'
                        ? { subject_ref: 'user-fixture', scopes: ['tasks.read', 'tasks.write'] }
                        : { conversation_ref: 'chat-fixture', turns: [] },
                ),
            )
        }),
    )
    mount()
    await waitFor(() => expect(screen.getByRole('button', { name: '发送目标 →' })).toBeDisabled())
    await userEvent.type(screen.getByLabelText('你的目标'), '查询余额')
    await waitFor(() => expect(screen.getByRole('button', { name: '发送目标 →' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: '发送目标 →' }))
    await screen.findByRole('button', { name: '核对并恢复原消息' })
    expect(screen.getByLabelText('你的目标')).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: '核对并恢复原消息' }))
    await waitFor(() => expect(writes).toHaveLength(2))
    expect(writes[0]).toBe(writes[1])
})
it('recovers a persisted pending message after reopening a conversation', async () => {
    let posted: Record<string, unknown> | undefined
    vi.stubGlobal(
        'fetch',
        vi.fn(async (input: string, init?: RequestInit) => {
            const path = new URL(input, 'http://localhost').pathname
            if (init?.method === 'POST') posted = JSON.parse(String(init.body))
            return new Response(
                JSON.stringify(
                    path === '/v1/me'
                        ? { subject_ref: 'user-fixture', scopes: ['tasks.read', 'tasks.write'] }
                        : {
                              conversation_ref: 'chat-fixture',
                              turns: [
                                  {
                                      sequence: 1,
                                      message_ref: 'message-pending',
                                      user_text: '查询余额',
                                      task_ref: null,
                                      ready: !!posted,
                                      reply: '已登记',
                                      created_at: '2026-10-29T00:00:00Z',
                                  },
                              ],
                          },
                ),
            )
        }),
    )
    mount('/tasks/chat/chat-fixture')
    await screen.findByText('正在处理原消息，请核对或恢复。')
    expect(screen.getByLabelText('继续对话')).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: '核对并恢复原消息' }))
    await waitFor(() =>
        expect(posted).toEqual({
            message_ref: 'message-pending',
            content: '查询余额',
            expected_sequence: 0,
        }),
    )
})
