import { useEffect } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { it, expect, vi, afterEach } from 'vitest'
import { Providers, useSession } from '../session'
import { BankWallet } from '../wallet'
function Login() {
    const { login } = useSession()
    useEffect(() => {
        void login('fixture')
    }, [])
    return null
}
afterEach(() => vi.unstubAllGlobals())
it('uses the selected bank and imports only selected server-provided assets', async () => {
    const writes: { path: string; body: unknown }[] = []
    const connection = {
        connection_ref: 'connection-test',
        bank_ref: 'bank-b',
        state: 'CONNECTED',
        bank_name: '银行乙',
    }
    const asset = {
        asset_ref: 'card:card-b',
        remote_ref: 'card-b',
        kind: 'card',
        display_name: '银行乙卡片',
        state: 'ACTIVE',
        connection_ref: 'connection-test',
        synced_at: '2026-10-29T00:00:00Z',
    }
    let imported = false
    vi.stubGlobal(
        'fetch',
        vi.fn(async (input: string, init?: RequestInit) => {
            const path = new URL(input, 'http://localhost').pathname
            let data: unknown = {}
            if (path === '/v1/me')
                data = {
                    subject_ref: 'fixture',
                    scopes: ['accounts.read', 'services.read', 'tasks.write'],
                }
            else if (path === '/v1/wallet') data = { connections: [], assets: [] }
            else if (path === '/v1/wallet/banks')
                data = ['a', 'b'].map((id) => ({
                    bank_ref: 'bank-' + id,
                    name: id === 'a' ? '银行甲' : '银行乙',
                    provider: 'fixture',
                    environment: 'simulation',
                    capabilities: ['accounts.read', 'cards.read'],
                    authorization: 'current_simulation_identity',
                }))
            else if (path.endsWith('/assets')) data = [asset]
            if (init?.method === 'POST') {
                writes.push({ path, body: JSON.parse(String(init.body)) })
                if (path === '/v1/wallet/connections') data = connection
                else {
                    imported = true
                    data = { connections: [connection], assets: [asset] }
                }
            }
            return new Response(JSON.stringify(data))
        }),
    )
    render(
        <Providers>
            <Login />
            <BankWallet />
        </Providers>,
    )
    const add = await screen.findByRole('button', { name: '＋ 添加银行卡' })
    await waitFor(() => expect(add).toBeEnabled())
    await userEvent.click(add)
    await screen.findByRole('option', { name: '银行乙（模拟）' })
    await userEvent.selectOptions(screen.getByLabelText('选择银行'), 'bank-b')
    await userEvent.click(screen.getByRole('button', { name: '连接所选银行' }))
    const checkbox = await screen.findByRole('checkbox', { name: /银行乙卡片/ })
    expect(writes[0].body).toMatchObject({ bank_ref: 'bank-b' })
    expect(screen.getByRole('button', { name: '导入所选资产' })).toBeDisabled()
    await userEvent.click(checkbox)
    await userEvent.click(screen.getByRole('button', { name: '导入所选资产' }))
    await waitFor(() => expect(imported).toBe(true))
    expect(writes[1]).toEqual({
        path: '/v1/wallet/connections/connection-test/import',
        body: { asset_refs: ['card:card-b'] },
    })
    await screen.findByText('银行乙 · 已连接')
})
