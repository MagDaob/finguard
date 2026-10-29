import { useEffect } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { afterEach, it, expect, vi } from 'vitest'
import { Providers, useSession } from '../session'
import { TaskMenu, TaskGroupPage } from '../task-groups'
function Login() {
    const { login } = useSession()
    useEffect(() => {
        void login('fixture')
    }, [])
    return null
}
afterEach(() => vi.unstubAllGlobals())
it('expands task categories without leaving chat and uses server counts', async () => {
    vi.stubGlobal(
        'fetch',
        vi.fn(async (input: string) => {
            const path = new URL(input, 'http://localhost').pathname
            return new Response(
                JSON.stringify(
                    path === '/v1/me'
                        ? { subject_ref: 'fixture', scopes: ['tasks.read', 'tasks.write'] }
                        : path.endsWith('/counts')
                          ? { pending: 41, completed: 26, ended: 2, all: 69 }
                          : [],
                ),
            )
        }),
    )
    render(
        <MemoryRouter initialEntries={['/tasks']}>
            <Providers>
                <Login />
                <TaskMenu />
                <Routes>
                    <Route path="/tasks" element={<p>聊天工作区</p>} />
                    <Route path="/tasks/list/:group" element={<TaskGroupPage />} />
                </Routes>
            </Providers>
        </MemoryRouter>,
    )
    const toggle = screen.getByRole('button', { name: '任务' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(toggle)
    await screen.findByText('41')
    expect(screen.getByText('聊天工作区')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('link', { name: '已完成 26' }))
    await screen.findByRole('heading', { name: '已完成' })
    await waitFor(() => expect(screen.getByText('此分类暂无任务')).toBeInTheDocument())
    await userEvent.click(toggle)
    expect(screen.queryByRole('link', { name: '已完成 26' })).not.toBeInTheDocument()
})
