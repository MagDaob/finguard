import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { ApiError, newRef, time } from './api'
import { useSession } from './session'
import { Confirm, Empty, ErrorNotice, Field, Loading, Modal, Notice, Panel } from './ui'
const bankSchema = z.object({
    bank_ref: z.string(),
    name: z.string(),
    provider: z.string(),
    environment: z.string(),
    capabilities: z.array(z.string()),
    authorization: z.string(),
})
const connectionSchema = z.object({
    connection_ref: z.string(),
    bank_ref: z.string(),
    state: z.string(),
    bank_name: z.string().optional(),
})
const availableSchema = z.object({
    asset_ref: z.string(),
    remote_ref: z.string(),
    kind: z.enum(['account', 'card']),
    display_name: z.string(),
    state: z.string(),
})
const walletSchema = z.object({
    connections: z.array(connectionSchema),
    assets: z.array(availableSchema.extend({ connection_ref: z.string(), synced_at: z.string() })),
})
type Available = z.infer<typeof availableSchema>
export function BankWallet() {
    const { api, me } = useSession(),
        q = useQueryClient(),
        key = [me?.subject_ref, 'wallet']
    const snapshot = useQuery({
        queryKey: key,
        enabled:
            !!api &&
            !!me?.scopes.includes('accounts.read') &&
            !!me.scopes.includes('services.read'),
        queryFn: ({ signal }) => api!.get('/v1/wallet', walletSchema, signal),
    })
    const [open, setOpen] = useState(false),
        [bank, setBank] = useState(''),
        [connection, setConnection] = useState(''),
        [assets, setAssets] = useState<Available[]>([]),
        [selected, setSelected] = useState<string[]>([]),
        [busy, setBusy] = useState(false),
        [error, setError] = useState<unknown>(),
        [uncertain, setUncertain] = useState(false),
        [remove, setRemove] = useState('')
    const lock = useRef(false),
        request = useRef(newRef())
    const catalog = useQuery({
        queryKey: [me?.subject_ref, 'wallet-banks'],
        enabled: !!api && open,
        queryFn: ({ signal }) => api!.get('/v1/wallet/banks', z.array(bankSchema), signal),
    })
    const chosen = catalog.data?.find((b) => b.bank_ref === bank),
        writable = !!me?.scopes.includes('tasks.write')
    async function perform(work: () => Promise<void>) {
        if (!api || lock.current) return
        lock.current = true
        setBusy(true)
        setError(null)
        try {
            await work()
        } catch (e) {
            setError(e)
        } finally {
            lock.current = false
            setBusy(false)
        }
    }
    function show(ref = '') {
        void snapshot.refetch()
        setError(null)
        setBank('')
        setConnection(ref)
        setAssets([])
        setSelected([])
        setUncertain(false)
        request.current = newRef()
        setOpen(true)
        if (ref)
            void perform(async () => {
                setAssets(
                    await api!.get(
                        `/v1/wallet/connections/${ref}/assets`,
                        z.array(availableSchema),
                    ),
                )
            })
    }
    async function connect() {
        await perform(async () => {
            let ref = connection
            try {
                if (!ref) {
                    const existing = snapshot.data?.connections.find(
                        (c) => c.bank_ref === bank && c.state === 'CONNECTED',
                    )
                    ref =
                        existing?.connection_ref ??
                        (
                            await api!.post(
                                '/v1/wallet/connections',
                                { request_ref: request.current, bank_ref: bank },
                                connectionSchema,
                            )
                        ).connection_ref
                    setConnection(ref)
                    setUncertain(false)
                    await q.invalidateQueries({ queryKey: key })
                }
                setAssets(
                    await api!.get(
                        `/v1/wallet/connections/${ref}/assets`,
                        z.array(availableSchema),
                    ),
                )
            } catch (e) {
                if (!ref && (!(e instanceof ApiError) || e.status === 0 || e.status >= 500)) {
                    setUncertain(true)
                    void q.invalidateQueries({ queryKey: key })
                }
                throw e
            }
        })
    }
    async function update(ref: string, action: 'sync' | 'disconnect') {
        await perform(async () => {
            const next = await api!.post(
                `/v1/wallet/connections/${ref}/${action}`,
                undefined,
                walletSchema,
            )
            q.setQueryData(key, next)
            setRemove('')
        })
    }
    return (
        <>
            <Panel title="已导入的账户与银行卡" tag="BANK CONNECTIONS">
                <div className="actions">
                    <button className="primary" disabled={!writable} onClick={() => show()}>
                        ＋ 添加银行卡
                    </button>
                </div>
                <ErrorNotice error={!open ? error : undefined} />
                <ErrorNotice error={snapshot.error} />
                {snapshot.isLoading && <Loading />}
                {snapshot.data && !snapshot.data.assets.length && (
                    <Empty title="尚未导入账户或银行卡">
                        选择已接入的银行，连接后导入该银行返回的账户或卡片。
                    </Empty>
                )}
                {snapshot.data?.connections
                    .filter((c) => c.state === 'CONNECTED')
                    .map((c) => (
                        <section className="wallet-bank" key={c.connection_ref}>
                            <h3>{c.bank_name} · 已连接</h3>
                            <small>连接编号：{c.connection_ref}</small>
                            <div className="wallet-assets">
                                {snapshot.data.assets
                                    .filter((a) => a.connection_ref === c.connection_ref)
                                    .map((a) => (
                                        <div className="wallet-asset" key={a.asset_ref}>
                                            <strong>{a.display_name}</strong>
                                            <span>
                                                {a.kind === 'account' ? '银行账户' : '银行卡'} ·{' '}
                                                {a.state === 'ACTIVE'
                                                    ? '正常'
                                                    : a.state === 'LOCKED'
                                                      ? '临时锁定'
                                                      : a.state === 'UNAVAILABLE'
                                                        ? '银行未返回该资产'
                                                        : a.state}
                                            </span>
                                            <small>资产引用：{a.remote_ref}</small>
                                            <small>最近同步：{time(a.synced_at)}</small>
                                        </div>
                                    ))}
                            </div>
                            <div className="actions">
                                <button
                                    className="secondary"
                                    disabled={busy || !writable}
                                    onClick={() => show(c.connection_ref)}
                                >
                                    添加账户或卡片
                                </button>
                                <button
                                    className="secondary"
                                    disabled={busy || !writable}
                                    onClick={() => void update(c.connection_ref, 'sync')}
                                >
                                    同步状态
                                </button>
                                <button
                                    className="text-button"
                                    disabled={busy || !writable}
                                    onClick={() => setRemove(c.connection_ref)}
                                >
                                    移除连接
                                </button>
                            </div>
                        </section>
                    ))}
            </Panel>
            {open && (
                <Modal
                    title="添加银行卡"
                    onClose={() => {
                        if (!busy) setOpen(false)
                    }}
                >
                    <Notice>
                        列表只显示已配置的银行。模拟银行使用当前模拟身份连接，不要求真实银行卡号或银行密码。
                    </Notice>
                    {!connection && (
                        <>
                            <Field label="选择银行">
                                <select
                                    aria-label="选择银行"
                                    value={bank}
                                    disabled={busy || uncertain}
                                    onChange={(e) => {
                                        setBank(e.target.value)
                                        request.current = newRef()
                                        setError(null)
                                    }}
                                >
                                    <option value="">请选择银行</option>
                                    {catalog.data?.map((b) => (
                                        <option key={b.bank_ref} value={b.bank_ref}>
                                            {b.name}
                                            {b.environment === 'simulation' ? '（模拟）' : ''}
                                        </option>
                                    ))}
                                </select>
                            </Field>
                            <ErrorNotice error={catalog.error} />
                            {catalog.isLoading && <Loading />}
                            {catalog.data?.length === 0 && <p>当前未配置可接入的银行。</p>}
                            {chosen && (
                                <p>
                                    连接器：{chosen.provider} ·
                                    支持账户与卡片读取。连接不等于付款授权。
                                </p>
                            )}
                        </>
                    )}
                    {connection && (
                        <>
                            <h3>选择要导入的资产</h3>
                            <p>仅按银行实际返回的类型显示；没有卡号时使用资产引用。</p>
                            {assets.map((a) => (
                                <label className="check" key={a.asset_ref}>
                                    <input
                                        type="checkbox"
                                        checked={selected.includes(a.asset_ref)}
                                        disabled={busy}
                                        onChange={(e) =>
                                            setSelected(
                                                e.target.checked
                                                    ? [...selected, a.asset_ref]
                                                    : selected.filter((r) => r !== a.asset_ref),
                                            )
                                        }
                                    />
                                    {a.display_name} · {a.kind === 'account' ? '账户' : '银行卡'} ·{' '}
                                    {a.remote_ref}
                                </label>
                            ))}
                            {!busy && !assets.length && <p>尚无可导入资产，可重新读取。</p>}
                        </>
                    )}
                    <ErrorNotice error={error} />
                    {uncertain && (
                        <Notice>
                            连接结果待核对，请复用原请求核对。重新打开后也可在已连接银行中继续导入。
                        </Notice>
                    )}
                    <div className="actions">
                        <button
                            className="secondary"
                            disabled={busy}
                            onClick={() => setOpen(false)}
                        >
                            关闭
                        </button>
                        <button
                            className="secondary"
                            disabled={busy || (!connection && !bank)}
                            onClick={() => void connect()}
                        >
                            {busy
                                ? '正在连接并读取…'
                                : connection
                                  ? '重新读取'
                                  : uncertain
                                    ? '核对原连接请求'
                                    : '连接所选银行'}
                        </button>
                        {connection && (
                            <button
                                className="primary"
                                disabled={busy || !selected.length}
                                onClick={() =>
                                    void perform(async () => {
                                        const next = await api!.post(
                                            `/v1/wallet/connections/${connection}/import`,
                                            { asset_refs: selected },
                                            walletSchema,
                                        )
                                        q.setQueryData(key, next)
                                        setOpen(false)
                                    })
                                }
                            >
                                导入所选资产
                            </button>
                        )}
                    </div>
                </Modal>
            )}
            {remove && (
                <Confirm
                    title="移除银行连接？"
                    description="从卡包移除此连接及其资产展示。不会销卡、关闭银行账户、撤回已执行交易或停止已有任务。"
                    onClose={() => setRemove('')}
                    onConfirm={() => void update(remove, 'disconnect')}
                    busy={busy}
                />
            )}
        </>
    )
}
