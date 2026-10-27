import { useEffect, useState } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { Providers, useSession } from '../session'
import { Composer, TaskDetail } from '../tasks'
import { Modal } from '../ui'

it('关闭弹窗后将键盘焦点还给触发按钮', async () => {
    function Fixture() {
        const [open, setOpen] = useState(false)
        return (
            <>
                <button onClick={() => setOpen(true)}>打开确认</button>
                {open && (
                    <Modal title="测试确认" onClose={() => setOpen(false)}>
                        确认内容
                    </Modal>
                )}
            </>
        )
    }
    render(<Fixture />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '打开确认' }))
    await user.click(screen.getByRole('button', { name: '关闭' }))
    expect(screen.getByRole('button', { name: '打开确认' })).toHaveFocus()
})

afterEach(() => vi.unstubAllGlobals())
const originalTask = {
    task_ref: 'task-fixture',
    subject_ref: 'user-fixture',
    user_request: '给小王转300元，至少保留1000元',
    status: 'WAITING_APPROVAL',
    current_version: 1,
    verified_success: false,
    stop_requested: false,
    created_at: '2026-10-27T09:00:00Z',
    updated_at: '2026-10-27T09:00:00Z',
    constraints: { minimum_remaining_balance_minor: 100000, maximum_fee_minor: 0 },
    questions: [],
    plans: [
        {
            version: 1,
            planner_version: 'fixture',
            steps: [
                {
                    step_ref: 'transfer',
                    action: 'transfer.create',
                    arguments: {},
                    depends_on: [],
                    funds_available_after: [],
                    operation_ref: 'operation-fixture',
                },
            ],
        },
    ],
    operations: [
        {
            operation_ref: 'operation-fixture',
            step_ref: 'transfer',
            action: 'transfer.create',
            arguments: {
                source_account_ref: 'account-fixture',
                recipient_ref: 'recipient-fixture',
                amount_minor: 30000,
                fee_minor: 0,
                minimum_remaining_balance_minor: 100000,
                currency: 'CNY',
            },
            digest: 'fixture-digest',
            status: 'WAITING_APPROVAL',
            result: null,
            error_code: null,
        },
    ],
    evidence: [],
}
const originalPreview = {
    operation_ref: 'operation-fixture',
    digest: 'fixture-digest',
    authentication_policy: {
        level: 'confirmation',
        step_up_required: false,
        step_up_available: false,
        threshold_minor: 100000,
    },
    preview: {
        recipient_name: '小王',
        available_balance_minor: 150000,
        arguments: originalTask.operations[0].arguments,
    },
}
function AutoLogin() {
    const { login } = useSession()
    useEffect(() => {
        void login('fixture-token')
    }, [])
    return null
}
function mount(content: React.ReactNode) {
    return render(
        <MemoryRouter>
            <Providers>
                <AutoLogin />
                {content}
            </Providers>
        </MemoryRouter>,
    )
}
function setup(
    options: {
        blocked?: boolean
        conflict?: boolean
        lostCreate?: boolean
        unknown?: boolean
    } = {},
) {
    let task = structuredClone(originalTask),
        previewReads = 0,
        creates = 0
    const writes: { path: string; body: unknown }[] = []
    if (options.unknown) {
        task.status = 'EXECUTING'
        task.operations[0].status = 'UNKNOWN'
    }
    vi.stubGlobal(
        'fetch',
        vi.fn(async (input: string, init?: RequestInit) => {
            const path = new URL(input, 'http://localhost').pathname,
                method = init?.method ?? 'GET'
            let data: unknown = {}
            if (method === 'POST') {
                writes.push({ path, body: init?.body ? JSON.parse(init.body as string) : null })
                if (path === '/v1/tasks') {
                    creates++
                    if (options.lostCreate && creates === 1) throw new Error('response lost')
                    data = task
                } else if (path.endsWith('/approvals')) data = { approval_ref: 'approval-fixture' }
                else if (path.endsWith('/run')) {
                    task = {
                        ...task,
                        status: 'COMPLETED',
                        verified_success: true,
                        operations: task.operations.map((op) => ({
                            ...op,
                            status: 'SUCCEEDED',
                            result: { balance_after_minor: 120000 } as never,
                        })),
                        evidence: [
                            {
                                evidence_ref: 'evidence-fixture',
                                operation_ref: 'operation-fixture',
                                kind: 'business_record',
                                source: 'fixture-bank',
                                record_ref: 'record-fixture',
                                data: {},
                                verified_at: '2026-10-27T09:01:00Z',
                            },
                        ] as never,
                    }
                    data = task
                }
            } else if (path === '/v1/me')
                data = {
                    subject_ref: 'user-fixture',
                    scopes: [
                        'accounts.read',
                        'tasks.read',
                        'tasks.write',
                        'approvals.write',
                        'transfers.write',
                    ],
                }
            else if (path === '/v1/accounts')
                data = [
                    {
                        account_ref: 'account-fixture',
                        balance_minor: 150000,
                        currency: 'CNY',
                        active: true,
                    },
                ]
            else if (path === '/v1/recipients')
                data = [{ recipient_ref: 'recipient-fixture', display_name: '小王', active: true }]
            else if (path.endsWith('/preview')) {
                previewReads++
                data = {
                    ...originalPreview,
                    authentication_policy: {
                        ...originalPreview.authentication_policy,
                        step_up_required: !!options.blocked,
                    },
                    preview: {
                        ...originalPreview.preview,
                        ...(options.conflict && previewReads > 1
                            ? { available_balance_minor: 140000 }
                            : {}),
                    },
                }
            } else if (path.endsWith('/approvals')) data = []
            else if (path === '/v1/tasks/task-fixture') data = task
            else if (path === '/v1/tasks') data = []
            else if (path === '/v1/delegations') data = []
            return new Response(JSON.stringify(data), {
                status: 200,
                headers: { 'Content-Type': 'application/json' },
            })
        }),
    )
    return writes
}
it('does not execute before confirmation and double clicks create only one authorization', async () => {
    const writes = setup()
    mount(<TaskDetail reference="task-fixture" />)
    const button = await screen.findByRole('button', { name: '确认转账 ¥300.00' })
    expect(writes).toHaveLength(0)
    await userEvent.dblClick(button)
    await screen.findByText('任务已完成，业务结果已核实')
    expect(writes.filter((w) => w.path.endsWith('/approvals'))).toEqual([
        {
            path: '/v1/operations/operation-fixture/approvals',
            body: { expected_digest: 'fixture-digest' },
        },
    ])
    expect(writes.filter((w) => w.path.endsWith('/run'))).toHaveLength(1)
})
it('blocks high risk confirmation using the server policy', async () => {
    const writes = setup({ blocked: true })
    mount(<TaskDetail reference="task-fixture" />)
    expect(await screen.findByRole('button', { name: '确认转账 ¥300.00' })).toBeDisabled()
    expect(screen.getByText(/此操作需要额外验证/)).toBeInTheDocument()
    expect(writes).toHaveLength(0)
})
it('shows changed preview and requires a new explicit acknowledgement without authorizing', async () => {
    const writes = setup({ conflict: true })
    mount(<TaskDetail reference="task-fixture" />)
    await userEvent.click(await screen.findByRole('button', { name: '确认转账 ¥300.00' }))
    await screen.findByText('请核对更新后的预览')
    expect(writes).toHaveLength(0)
    expect(screen.getByRole('button', { name: '确认转账 ¥300.00' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: '我已核对新旧参数' })).not.toBeChecked()
})
it('does not offer a second payment for an unknown result', async () => {
    setup({ unknown: true })
    mount(<TaskDetail reference="task-fixture" />)
    await screen.findByText('结果待核实', { exact: true })
    expect(screen.queryByRole('button', { name: /确认转账/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '核实原操作' })).toBeInTheDocument()
})
it('retains the identical request identity and payload after a lost creation response', async () => {
    const writes = setup({ lostCreate: true }),
        close = vi.fn()
    mount(<Composer onClose={close} />)
    await waitFor(() => expect(screen.getByRole('button', { name: '生成任务计划' })).toBeEnabled())
    await userEvent.type(screen.getByRole('textbox', { name: '原始目标' }), '给小王转300元')
    await userEvent.click(screen.getByRole('button', { name: '生成任务计划' }))
    await screen.findByRole('button', { name: '核对原请求' })
    expect(screen.getByRole('textbox', { name: '原始目标' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: '核对原请求' }))
    await waitFor(() => expect(close).toHaveBeenCalledOnce())
    expect(writes).toHaveLength(2)
    expect(writes[0].body).toEqual(writes[1].body)
})
