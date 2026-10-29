import { useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query'
import { z } from 'zod'
import {
    ArrowDownLeft,
    ArrowUpRight,
    CreditCard,
    FileChartColumnIncreasing,
    Landmark,
    Plus,
    RefreshCw,
    ShieldCheck,
    Wallet,
} from 'lucide-react'
import {
    ApiError,
    delegationSchema,
    ledgerSchema,
    objectSchema,
    objectsSchema,
    productSchema,
    resourceSchema,
    text,
    time,
    type Step,
} from './api'
import { decimal, isMinor, remaining, yuan } from './money'
import { useAccounts, useRecipients, useRefresh } from './hooks'
import { useSession } from './session'
import { Composer } from './tasks'
import { BankWallet } from './wallet'
import {
    Badge,
    Confirm,
    Empty,
    ErrorNotice,
    Field,
    Loading,
    Modal,
    Money,
    Notice,
    Panel,
    Raw,
    Updated,
} from './ui'

const makeStep = (action: string, args: Record<string, unknown>): Step => ({
    step_ref: 'step-1',
    action,
    arguments: args,
    depends_on: [],
    funds_available_after: [],
})
function usePreset() {
    const [preset, setPreset] = useState<{ title: string; steps: Step[] } | null>(null)
    return {
        preset,
        setPreset,
        modal: preset && <Composer preset={preset} onClose={() => setPreset(null)} />,
    }
}
function chinaMonth() {
    const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
    }).formatToParts(new Date())
    return `${parts.find((x) => x.type === 'year')!.value}-${parts.find((x) => x.type === 'month')!.value}`
}
export function monthWindow(value: string) {
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(value)) throw new Error('请选择有效月份。')
    const [year, month] = value.split('-').map(Number)
    if (year < 2000 || year > 2100) throw new Error('年份须为 2000 至 2100。')
    return {
        start_date: `${value}-01`,
        end_date: new Date(Date.UTC(year, month, 1)).toISOString().slice(0, 10),
        timezone: 'Asia/Shanghai',
    }
}
export function AccountsPage() {
    const { accountRef } = useParams(),
        accounts = useAccounts(),
        navigate = useNavigate(),
        { api, me } = useSession(),
        { setPreset, modal } = usePreset(),
        [month, setMonth] = useState(chinaMonth()),
        [category, setCategory] = useState(''),
        [error, setError] = useState<unknown>()
    const selected = accounts.data?.find((a) => a.account_ref === accountRef) ?? accounts.data?.[0]
    const ledger = useInfiniteQuery({
        queryKey: [me?.subject_ref, 'ledger', selected?.account_ref],
        enabled: !!api && !!selected && !!me?.scopes.includes('transactions.read'),
        initialPageParam: '',
        queryFn: ({ pageParam, signal }) =>
            api!.get(
                `/v1/accounts/${selected!.account_ref}/ledger?limit=25` +
                    (pageParam ? '&before=' + pageParam : ''),
                z.array(ledgerSchema),
                signal,
            ),
        getNextPageParam: (last) => (last.length === 25 ? last.at(-1)?.entry_ref : undefined),
    })
    return (
        <>
            <div className="page-title">
                <div>
                    <span className="eyebrow">ACCOUNTS & STATEMENTS</span>
                    <h1>账单明细</h1>
                    <p>当前余额与已入账流水，分别读取、清楚呈现。</p>
                </div>
                <button
                    className="secondary"
                    onClick={() => {
                        void accounts.refetch()
                        void ledger.refetch()
                    }}
                >
                    <RefreshCw size={16} />
                    刷新账户
                </button>
            </div>
            <ErrorNotice error={accounts.error} />
            <div className="account-cards">
                {accounts.data?.map((a, i) => (
                    <button
                        className={`account-card ${selected?.account_ref === a.account_ref ? 'selected' : ''}`}
                        key={a.account_ref}
                        onClick={() => navigate('/accounts/' + a.account_ref)}
                    >
                        <div>
                            <span className="account-symbol">
                                <Wallet size={23} />
                            </span>
                            <Badge status={a.active ? 'success' : 'danger'}>
                                {a.active ? '账户正常' : '账户不可用'}
                            </Badge>
                        </div>
                        <span>模拟账户 {String(i + 1).padStart(2, '0')}</span>
                        <Money value={a.balance_minor} large />
                        <small>{a.account_ref}</small>
                    </button>
                ))}
            </div>
            <Updated at={accounts.dataUpdatedAt} stale={!!accounts.error} />
            {accounts.isLoading && <Loading />}
            <div className="business-grid">
                <Panel title="按月分析账单" tag="STATEMENT">
                    <p className="muted">
                        支出、退款与净支出由业务工具完整统计，非仅汇总当前已加载的流水。
                    </p>
                    <form
                        onSubmit={(e) => {
                            e.preventDefault()
                            setError(null)
                            try {
                                if (!selected) throw new Error('请先选择可访问的账户。')
                                setPreset({
                                    title: `${month} ${category || '全部分类'}账单分析`,
                                    steps: [
                                        makeStep('bills.analyze', {
                                            account_ref: selected.account_ref,
                                            ...monthWindow(month),
                                            ...(category ? { category } : {}),
                                        }),
                                    ],
                                })
                            } catch (e) {
                                setError(e)
                            }
                        }}
                    >
                        <div className="form-grid">
                            <Field label="自然月（上海时区）">
                                <input
                                    type="month"
                                    required
                                    value={month}
                                    onChange={(e) => setMonth(e.target.value)}
                                />
                            </Field>
                            <Field label="消费分类">
                                <select
                                    value={category}
                                    onChange={(e) => setCategory(e.target.value)}
                                >
                                    <option value="">全部分类</option>
                                    <option value="food">餐饮</option>
                                    <option value="transport">交通</option>
                                    <option value="shopping">购物</option>
                                </select>
                            </Field>
                        </div>
                        <ErrorNotice error={error} />
                        <button className="primary" disabled={!selected}>
                            <FileChartColumnIncreasing size={17} />
                            生成账单任务
                        </button>
                    </form>
                    <Notice>
                        日期区间从当月 1 日开始，至下月 1 日前结束。无匹配记录时显示零。
                    </Notice>
                </Panel>
                <Panel title="已加载交易流水" tag="POSTED CASH FLOW">
                    <ErrorNotice error={ledger.error} />
                    {ledger.isLoading && <Loading />}
                    <div className="ledger-list">
                        {ledger.data?.pages.flat().map((row) => (
                            <div key={row.entry_ref}>
                                <span className="ledger-icon">
                                    {BigInt(row.amount_minor) < 0n ? (
                                        <ArrowUpRight size={17} />
                                    ) : (
                                        <ArrowDownLeft size={17} />
                                    )}
                                </span>
                                <div>
                                    <strong>{row.category}</strong>
                                    <small>{time(row.occurred_at)}</small>
                                </div>
                                <Money value={row.amount_minor} />
                            </div>
                        ))}
                    </div>
                    {ledger.data && !ledger.data.pages.flat().length && (
                        <Empty title="当前页没有入账记录" />
                    )}
                    {ledger.hasNextPage && (
                        <button
                            className="secondary"
                            disabled={ledger.isFetchingNextPage}
                            onClick={() => void ledger.fetchNextPage()}
                        >
                            加载更多流水
                        </button>
                    )}
                    <small className="muted">仅展示已加载范围，不作为全部账单统计。</small>
                </Panel>
            </div>
            {modal}
        </>
    )
}

export function ProductsPage() {
    const { api, me } = useSession(),
        accounts = useAccounts(),
        { setPreset, modal } = usePreset(),
        [choice, setChoice] = useState(''),
        [amount, setAmount] = useState('100.00'),
        [error, setError] = useState<unknown>()
    const products = useQuery({
        queryKey: [me?.subject_ref, 'products'],
        enabled: !!api && !!me?.scopes.includes('products.read'),
        queryFn: () => api!.get('/v1/products', z.array(productSchema)),
    })
    const positions = useQuery({
        queryKey: [me?.subject_ref, 'positions'],
        enabled: !!api && !!me?.scopes.includes('products.read'),
        queryFn: () => api!.get('/v1/positions', objectsSchema),
    })
    const order = (product: string, action: string) => {
        setError(null)
        try {
            const account = choice || accounts.data?.[0]?.account_ref
            if (!account) throw new Error('请先选择账户。')
            const value = yuan(amount)
            if (!value) throw new Error('申赎金额须大于零。')
            setPreset({
                title: `${action === 'product.redeem' ? '赎回' : '申购'}${products.data?.find((p) => p.product_ref === product)?.name ?? product} ${amount} 元`,
                steps: [
                    makeStep(action, {
                        account_ref: account,
                        product_ref: product,
                        amount_minor: value,
                        currency: 'CNY',
                        fee_minor: 0,
                        minimum_remaining_balance_minor: 0,
                    }),
                ],
            })
        } catch (e) {
            setError(e)
        }
    }
    return (
        <>
            <div className="page-title">
                <div>
                    <span className="eyebrow">PRODUCTS & HOLDINGS</span>
                    <h1>产品与持仓</h1>
                    <p>固定价值模拟产品，风险与交易条件由服务端复检。</p>
                </div>
            </div>
            <Notice>模拟环境不提供净值行情、预测收益或投资建议。赎回受理不代表资金到账。</Notice>
            <div className="compact-form">
                <Field label="操作账户">
                    <select
                        value={choice || accounts.data?.[0]?.account_ref || ''}
                        onChange={(e) => setChoice(e.target.value)}
                    >
                        {accounts.data?.map((a) => (
                            <option key={a.account_ref}>{a.account_ref}</option>
                        ))}
                    </select>
                </Field>
                <Field label="申赎金额（元）">
                    <input
                        inputMode="decimal"
                        value={amount}
                        onChange={(e) => setAmount(e.target.value)}
                    />
                </Field>
            </div>
            <ErrorNotice error={error ?? products.error ?? positions.error} />
            <div className="product-grid">
                {products.data?.map((p) => {
                    const account = choice || accounts.data?.[0]?.account_ref
                    const holdings = positions.data?.filter(
                        (r) => r.product_ref === p.product_ref && r.account_ref === account,
                    )
                    const value =
                        account && holdings && holdings.every((r) => isMinor(r.value_minor))
                            ? holdings.reduce(
                                  (sum, r) => sum + BigInt(r.value_minor as number | bigint),
                                  0n,
                              )
                            : undefined
                    return (
                        <Panel key={p.product_ref} title={p.name} tag="SIMULATED PRODUCT">
                            <div className="product-symbol">
                                <Landmark size={27} />
                            </div>
                            <div className="inline">
                                <Badge status={p.risk_eligible ? 'success' : 'warning'}>
                                    {p.risk_eligible ? '风险等级匹配' : '风险等级不匹配'}
                                </Badge>
                                <Badge status={p.trading_open ? 'neutral' : 'danger'}>
                                    {p.trading_open ? '交易窗口开放' : '交易窗口关闭'}
                                </Badge>
                            </div>
                            <dl className="facts">
                                <dt>产品风险等级</dt>
                                <dd>{p.risk_level}</dd>
                                <dt>已加载持仓</dt>
                                <dd>
                                    <Money value={value} />
                                </dd>
                            </dl>
                            <div className="actions">
                                <button
                                    className="primary"
                                    disabled={!account || !p.risk_eligible || !p.trading_open}
                                    onClick={() => order(p.product_ref, 'product.subscribe')}
                                >
                                    准备申购
                                </button>
                                <button
                                    className="secondary"
                                    disabled={!p.trading_open || value === undefined || value <= 0n}
                                    onClick={() => order(p.product_ref, 'product.redeem')}
                                >
                                    准备赎回
                                </button>
                            </div>
                        </Panel>
                    )
                })}
            </div>
            {modal}
        </>
    )
}

export function ServicesPage() {
    const { api, me } = useSession(),
        { setPreset, modal } = usePreset()
    const cards = useQuery({
        queryKey: [me?.subject_ref, 'services', 'cards'],
        enabled: !!api && !!me?.scopes.includes('services.read'),
        queryFn: () => api!.get('/v1/cards', z.array(resourceSchema)),
    })
    const subscriptions = useQuery({
        queryKey: [me?.subject_ref, 'services', 'subscriptions'],
        enabled: !!api && !!me?.scopes.includes('services.read'),
        queryFn: () => api!.get('/v1/subscriptions', z.array(resourceSchema)),
    })
    return (
        <>
            <div className="page-title">
                <div>
                    <span className="eyebrow">CARDS & SUBSCRIPTIONS</span>
                    <h1>卡包</h1>
                    <p>服务状态来自模拟银行，变更仍需生成任务并确认。</p>
                </div>
            </div>
            <BankWallet />
            <ErrorNotice error={cards.error ?? subscriptions.error} />
            <div className="business-grid">
                <Panel title="卡片管理" tag="TEMPORARY LOCK">
                    {cards.data?.map((c) => (
                        <div className="service-card" key={c.resource_ref}>
                            <CreditCard size={29} />
                            <h3>模拟卡片</h3>
                            <small>{c.resource_ref}</small>
                            <Badge status={c.state === 'ACTIVE' ? 'success' : 'warning'}>
                                {c.state === 'ACTIVE'
                                    ? '正常'
                                    : c.state === 'LOCKED'
                                      ? '临时锁定'
                                      : c.state}
                            </Badge>
                            <p>临时锁定不等同于永久挂失或销卡。恢复也会创建新任务并重新授权。</p>
                            <button
                                className="secondary"
                                disabled={!['ACTIVE', 'LOCKED'].includes(c.state)}
                                onClick={() =>
                                    setPreset({
                                        title:
                                            c.state === 'LOCKED'
                                                ? '解除卡片临时锁定'
                                                : '临时锁定卡片',
                                        steps: [
                                            makeStep('card.set_lock', {
                                                card_ref: c.resource_ref,
                                                locked: c.state !== 'LOCKED',
                                            }),
                                        ],
                                    })
                                }
                            >
                                {c.state === 'LOCKED' ? '准备解除锁定' : '准备临时锁定'}
                            </button>
                        </div>
                    ))}
                </Panel>
                <Panel title="订阅合同" tag="SUBSCRIPTIONS">
                    {subscriptions.data?.map((s) => (
                        <div className="service-card" key={s.resource_ref}>
                            <h3>{text(s.data.merchant, '模拟订阅')}</h3>
                            <Badge status={s.state === 'ACTIVE' ? 'success' : 'neutral'}>
                                {s.state === 'ACTIVE'
                                    ? '合同有效'
                                    : s.state === 'CANCELLED'
                                      ? '合同已取消'
                                      : s.state}
                            </Badge>
                            <Money value={s.data.period_price_minor} />
                            <p>取消仅作用于订阅合同。退款为零，代扣授权保持不变。</p>
                            <button
                                className="secondary"
                                disabled={s.state !== 'ACTIVE'}
                                onClick={() =>
                                    setPreset({
                                        title:
                                            '取消订阅合同：' +
                                            text(s.data.merchant, s.resource_ref),
                                        steps: [
                                            makeStep('subscription.cancel', {
                                                subscription_ref: s.resource_ref,
                                            }),
                                        ],
                                    })
                                }
                            >
                                准备取消订阅
                            </button>
                        </div>
                    ))}
                </Panel>
            </div>
            {modal}
        </>
    )
}

export function AuthorizationsPage() {
    const createLock = useRef(false)
    const { api, me } = useSession(),
        refresh = useRefresh(),
        accounts = useAccounts(),
        recipients = useRecipients()
    const [open, setOpen] = useState(false),
        [revoke, setRevoke] = useState(''),
        [error, setError] = useState<unknown>(),
        [uncertain, setUncertain] = useState(false)
    const list = useInfiniteQuery({
        queryKey: [me?.subject_ref, 'delegations'],
        enabled: !!api && !!me?.scopes.includes('approvals.write'),
        initialPageParam: '',
        queryFn: ({ pageParam }) =>
            api!.get(
                '/v1/delegations?limit=25' + (pageParam ? '&before=' + pageParam : ''),
                z.array(delegationSchema),
            ),
        getNextPageParam: (last) => (last.length === 25 ? last.at(-1)?.delegation_ref : undefined),
    })
    const [account, setAccount] = useState(''),
        [selected, setSelected] = useState<string[]>([]),
        [single, setSingle] = useState('300.00'),
        [total, setTotal] = useState('600.00'),
        [count, setCount] = useState('2'),
        [period, setPeriod] = useState('3600'),
        [ttl, setTtl] = useState('3600'),
        [ack, setAck] = useState(false)
    const create = useMutation({
        mutationFn: async () => {
            if (createLock.current) throw new Error('正在建立委托，请勿重复提交。')
            if (uncertain) throw new Error('此前创建结果待核对，不能重复提交委托。')
            if (!api || !ack || !selected.length)
                throw new Error('请确认收款人白名单与全部委托限制。')
            const a = account || accounts.data?.[0]?.account_ref
            if (!a) throw new Error('请选择账户。')
            createLock.current = true
            try {
                await api.post(
                    '/v1/delegations',
                    {
                        source_account_ref: a,
                        recipient_refs: selected,
                        per_operation_limit_minor: yuan(single),
                        total_limit_minor: yuan(total),
                        maximum_operations: Number(count),
                        period_seconds: Number(period),
                        ttl_seconds: Number(ttl),
                    },
                    objectSchema,
                )
                await refresh()
                setOpen(false)
                setAck(false)
            } finally {
                createLock.current = false
            }
        },
        onError: (e) => {
            setError(e)
            if (e instanceof ApiError && (e.status === 0 || e.status >= 500)) setUncertain(true)
        },
    })
    const revocation = useMutation({
        mutationFn: async () => {
            await api!.post(`/v1/delegations/${revoke}/revoke`, undefined, objectSchema)
            await refresh()
            setRevoke('')
        },
        onError: setError,
    })
    return (
        <>
            <div className="page-title">
                <div>
                    <span className="eyebrow">AUTHORIZATION BOUNDARIES</span>
                    <h1>授权管理</h1>
                    <p>查看受限委托。单次授权记录保留在各自任务中。</p>
                </div>
                <button
                    className="primary"
                    onClick={() => setOpen(true)}
                    disabled={
                        create.isPending || uncertain || !me?.scopes.includes('approvals.write')
                    }
                >
                    <Plus size={17} />
                    建立受限委托
                </button>
            </div>
            <Notice>
                单笔、累计、频次、期限和收款人限制同时生效。未知操作占用预算；明确失败释放预算，但保留尝试次数。此处不代表全部待确认任务。
            </Notice>
            <ErrorNotice error={error ?? list.error} />
            {uncertain && (
                <Notice kind="warning">
                    委托创建结果待核对，已停用本会话的新建动作。请读取现有委托，不重复提交。
                </Notice>
            )}
            <div className="delegations">
                {list.data?.pages.flat().map((g) => (
                    <Panel
                        key={g.delegation_ref}
                        title="受限转账委托"
                        tag={g.delegation_ref.slice(-9)}
                        actions={
                            <Badge
                                status={
                                    g.revoked
                                        ? 'danger'
                                        : Date.parse(g.expires_at) < Date.now()
                                          ? 'neutral'
                                          : 'success'
                                }
                            >
                                {g.revoked
                                    ? '已撤销'
                                    : Date.parse(g.expires_at) < Date.now()
                                      ? '已到期'
                                      : '有效'}
                            </Badge>
                        }
                    >
                        <dl className="facts">
                            <dt>付款账户</dt>
                            <dd>{text(g.specification.source_account_ref)}</dd>
                            <dt>收款人白名单</dt>
                            <dd>
                                {Array.isArray(g.specification.recipient_refs)
                                    ? g.specification.recipient_refs.join('、')
                                    : '未提供'}
                            </dd>
                            <dt>单笔限额</dt>
                            <dd>
                                <Money value={g.specification.per_operation_limit_minor} />
                            </dd>
                            <dt>累计限额</dt>
                            <dd>
                                <Money value={g.specification.total_limit_minor} />
                            </dd>
                            <dt>已占用</dt>
                            <dd>
                                <Money value={g.used_minor} />
                            </dd>
                            <dt>剩余预算</dt>
                            <dd>
                                <Money
                                    value={remaining(
                                        g.specification.total_limit_minor,
                                        g.used_minor,
                                        0,
                                    )}
                                />
                            </dd>
                            <dt>频次限制</dt>
                            <dd>
                                {String(g.specification.maximum_operations)} 次 /{' '}
                                {String(g.specification.period_seconds)} 秒
                            </dd>
                            <dt>到期时间</dt>
                            <dd>{time(g.expires_at)}</dd>
                        </dl>
                        <button
                            className="secondary"
                            disabled={g.revoked || Date.parse(g.expires_at) < Date.now()}
                            onClick={() => setRevoke(g.delegation_ref)}
                        >
                            撤销委托
                        </button>
                    </Panel>
                ))}
            </div>
            {list.data && !list.data.pages.flat().length && (
                <Empty title="尚无已加载的受限委托">每次操作仍可在任务中单独确认。</Empty>
            )}
            {list.hasNextPage && (
                <button
                    className="secondary"
                    disabled={list.isFetchingNextPage}
                    onClick={() => void list.fetchNextPage()}
                >
                    加载更多委托
                </button>
            )}
            {open && (
                <Modal title="建立受限转账委托" onClose={() => setOpen(false)}>
                    <form
                        onSubmit={(e) => {
                            e.preventDefault()
                            create.mutate()
                        }}
                    >
                        <Field label="付款账户">
                            <select
                                value={account || accounts.data?.[0]?.account_ref || ''}
                                onChange={(e) => setAccount(e.target.value)}
                            >
                                {accounts.data?.map((a) => (
                                    <option key={a.account_ref}>{a.account_ref}</option>
                                ))}
                            </select>
                        </Field>
                        <div className="field">
                            <span>收款人白名单</span>
                            {recipients.data
                                ?.filter((r) => r.active)
                                .map((r) => (
                                    <label className="check" key={r.recipient_ref}>
                                        <input
                                            type="checkbox"
                                            checked={selected.includes(r.recipient_ref)}
                                            onChange={(e) =>
                                                setSelected(
                                                    e.target.checked
                                                        ? [...selected, r.recipient_ref]
                                                        : selected.filter(
                                                              (x) => x !== r.recipient_ref,
                                                          ),
                                                )
                                            }
                                        />
                                        {r.display_name}
                                    </label>
                                ))}
                        </div>
                        <div className="form-grid">
                            {[
                                ['单笔上限（元）', single, setSingle],
                                ['累计上限（元）', total, setTotal],
                                ['最多操作次数', count, setCount],
                                ['频次周期（秒）', period, setPeriod],
                                ['有效期（秒）', ttl, setTtl],
                            ].map(([label, value, set]) => (
                                <Field label={label as string} key={label as string}>
                                    <input
                                        inputMode="decimal"
                                        value={value as string}
                                        onChange={(e) =>
                                            (set as (s: string) => void)(e.target.value)
                                        }
                                        required
                                    />
                                </Field>
                            ))}
                        </div>
                        <label className="check">
                            <input
                                type="checkbox"
                                checked={ack}
                                onChange={(e) => setAck(e.target.checked)}
                            />
                            我明确同意以上全部委托范围与限制
                        </label>
                        <ErrorNotice error={error} />
                        <button
                            className="primary"
                            disabled={uncertain || create.isPending || !ack}
                        >
                            确认建立委托
                        </button>
                    </form>
                </Modal>
            )}
            {revoke && (
                <Confirm
                    title="撤销这份委托？"
                    description="撤销后不能用于新提交；已经提交的操作继续核实。"
                    onClose={() => setRevoke('')}
                    onConfirm={() => revocation.mutate()}
                    busy={revocation.isPending}
                />
            )}
        </>
    )
}
