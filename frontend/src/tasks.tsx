import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import {
    ArrowDownLeft,
    ArrowRight,
    Check,
    CheckCircle2,
    ChevronRight,
    ListFilter,
    RefreshCw,
    ShieldCheck,
    Square,
    X,
} from 'lucide-react'
import {
    ApiError,
    approvalSchema,
    delegationSchema,
    eventSchema,
    newRef,
    objectSchema,
    stepSchema,
    taskSchema,
    text,
    time,
    type Operation,
    type Preview,
    type Step,
    type Task,
    type TaskInput,
} from './api'
import { json, isMinor, money, remaining, yuan, decimal, canonical } from './money'
import { useAccounts, useRecipients, useRefresh, useTask, useTasks } from './hooks'
import { useSession } from './session'
import {
    actionName,
    activeOperations,
    editable,
    isWrite,
    operationLabel,
    operationLabels,
    stopMessage,
    taskLabel,
    taskLabels,
    verified,
} from './state'
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
    VerifiedMark,
} from './ui'

export const inputStep = (s: Step): Step => ({
    step_ref: s.step_ref,
    action: s.action,
    arguments: s.arguments,
    depends_on: s.depends_on ?? [],
    funds_available_after: s.funds_available_after ?? [],
})
export function TaskHistory() {
    const { taskRef } = useParams(),
        rows = useTasks()
    const [filter, setFilter] = useState('')
    const tasks = rows.data?.pages.flat() ?? [],
        shown = tasks.filter((t) => !filter || t.status === filter)
    return (
        <section className="task-rail" aria-label="任务历史">
            <div className="rail-head">
                <h2>
                    我的任务 <span>{tasks.length}</span>
                </h2>
                <ListFilter size={16} />
            </div>
            <label className="sr-only" htmlFor="task-filter">
                筛选已加载任务
            </label>
            <select id="task-filter" value={filter} onChange={(e) => setFilter(e.target.value)}>
                <option value="">全部已加载任务</option>
                <option value="WAITING_APPROVAL">等待确认</option>
                <option value="EXECUTING">正在执行</option>
                <option value="COMPLETED">已标记完成</option>
                <option value="WAITING_CLARIFICATION">需要补充信息</option>
            </select>
            <small className="rail-note">按引用分页，筛选仅限已加载记录</small>
            <ErrorNotice error={rows.error} />
            {rows.isLoading && <Loading />}
            <div className="rail-items">
                {shown.map((t) => (
                    <Link
                        to={'/tasks/' + t.task_ref}
                        className={`task-item ${t.task_ref === taskRef ? 'selected' : ''}`}
                        key={t.task_ref}
                    >
                        <div>
                            <Badge status={t.status}>
                                {t.status === 'PLANNING' && t.current_version > 0
                                    ? '计划已生成'
                                    : t.status === 'COMPLETED'
                                      ? '已标记完成'
                                      : (taskLabels[t.status] ?? '暂无法识别')}
                            </Badge>
                            <ChevronRight size={15} />
                        </div>
                        <strong>{t.user_request}</strong>
                        <small>{time(t.created_at)}</small>
                    </Link>
                ))}
            </div>
            {!rows.isLoading && !tasks.length && <p className="rail-note">尚无已加载任务</p>}
            {rows.hasNextPage && (
                <button
                    className="text-button"
                    disabled={rows.isFetchingNextPage}
                    onClick={() => void rows.fetchNextPage()}
                >
                    加载更多
                </button>
            )}
        </section>
    )
}
export function TasksPage() {
    const { taskRef } = useParams(),
        location = useLocation(),
        navigate = useNavigate()
    const [preset, setPreset] = useState<{ steps: Step[]; title: string } | null>(null)
    useEffect(() => {
        if (location.state?.preset) {
            setPreset(location.state.preset)
            navigate(location.pathname, { replace: true, state: null })
        }
    }, [location, navigate])
    return taskRef ? (
        <TaskDetail key={taskRef} reference={taskRef} />
    ) : (
        <section className="goal-home">
            <div className="goal-heading">
                <span className="eyebrow">PLAN. CONFIRM. VERIFY.</span>
                <h1>
                    想完成
                    <br />
                    <em>什么？</em>
                </h1>
                <p>说出目标，查看计划，确认后执行。</p>
                <div className="geometry" aria-hidden="true">
                    <i />
                    <b />
                    <span />
                </div>
            </div>
            <Composer
                key={location.key + (preset?.title ?? 'new')}
                preset={preset}
                inline
                onClose={() => setPreset(null)}
            />
        </section>
    )
}

export function Composer({
    preset,
    onClose,
    task,
    inline = false,
}: {
    preset?: { steps: Step[]; title: string } | null
    onClose: () => void
    task?: Task
    inline?: boolean
}) {
    const { api } = useSession(),
        navigate = useNavigate(),
        refresh = useRefresh(),
        accounts = useAccounts(),
        recipients = useRecipients()
    const prior = task ? activeOperations(task) : [],
        priorTransfer =
            prior.length === 1 && prior[0].action === 'transfer.create' && editable(prior[0])
                ? prior[0]
                : null
    const [mode, setMode] = useState(
            preset ? 'preset' : priorTransfer ? 'transfer' : task ? 'structured' : 'natural',
        ),
        [message, setMessage] = useState(preset?.title ?? task?.user_request ?? ''),
        [account, setAccount] = useState(text(priorTransfer?.arguments.source_account_ref, '')),
        [recipient, setRecipient] = useState(text(priorTransfer?.arguments.recipient_ref, '')),
        [amount, setAmount] = useState(
            isMinor(priorTransfer?.arguments.amount_minor)
                ? decimal(priorTransfer.arguments.amount_minor)
                : '300.00',
        ),
        [reserve, setReserve] = useState(
            isMinor(priorTransfer?.arguments.minimum_remaining_balance_minor)
                ? decimal(priorTransfer.arguments.minimum_remaining_balance_minor)
                : '1000.00',
        ),
        [script, setScript] = useState(
            json((preset?.steps ?? task?.plans.at(-1)?.steps ?? []).map(inputStep), true),
        ),
        [reviewed, setReviewed] = useState(false),
        [error, setError] = useState<unknown>(),
        [busy, setBusy] = useState(false),
        [uncertain, setUncertain] = useState(false)
    const lock = useRef(false),
        pending = useRef<TaskInput | null>(null)
    const submit = async (e: FormEvent) => {
        e.preventDefault()
        if (lock.current || !api) return
        lock.current = true
        setBusy(true)
        setError(null)
        let created: Task | undefined
        try {
            let body: TaskInput
            if (pending.current) body = pending.current
            else {
                body = { client_request_ref: newRef(), user_request: message.trim() }
                if (mode === 'transfer') {
                    const a = account || accounts.data?.[0]?.account_ref,
                        r = recipient || recipients.data?.find((x) => x.active)?.recipient_ref
                    if (!a || !r) throw new Error('请确认付款账户与收款人。')
                    const value = yuan(amount)
                    if (value <= 0) throw new Error('转账金额须大于零。')
                    body = {
                        ...body,
                        user_request: `向${recipients.data?.find((x) => x.recipient_ref === r)?.display_name ?? r}转账 ${amount} 元`,
                        steps: [
                            {
                                step_ref: priorTransfer?.step_ref ?? 'transfer',
                                action: 'transfer.create',
                                arguments: {
                                    source_account_ref: a,
                                    recipient_ref: r,
                                    amount_minor: value,
                                    currency: 'CNY',
                                    fee_minor: 0,
                                    minimum_remaining_balance_minor: yuan(reserve),
                                },
                                depends_on: [],
                                funds_available_after: [],
                            },
                        ],
                    }
                }
                if (mode === 'preset' || mode === 'structured') {
                    body.steps = z
                        .array(stepSchema)
                        .min(1)
                        .max(32)
                        .parse(JSON.parse(script))
                        .map(inputStep)
                    if (!reviewed) throw new Error('请逐项确认完整目标后提交。')
                }
                if (!body.user_request) throw new Error('请填写本次任务目标。')
                pending.current = body
            }
            if (task) {
                if (!reviewed || !body.steps) throw new Error('请核对全部目标。')
                for (const op of activeOperations(task).filter((op) =>
                    ['SUCCEEDED', 'SUBMITTING', 'UNKNOWN'].includes(op.status),
                )) {
                    const kept = body.steps.find((s) => s.step_ref === op.step_ref)
                    if (
                        !kept ||
                        kept.action !== op.action ||
                        canonical(kept.arguments) !== canonical(op.arguments)
                    )
                        throw new Error('已完成或待核实的操作必须原样保留。')
                }
                created = await api.post(
                    `/v1/tasks/${task.task_ref}/plans`,
                    { steps: body.steps, expected_version: task.current_version },
                    taskSchema,
                )
            } else created = await api.post('/v1/tasks', body, taskSchema)
            setUncertain(false)
            await refresh(created)
            navigate('/tasks/' + created.task_ref)
            onClose()
            if (
                created.operations.length &&
                activeOperations(created).every((op) => !isWrite(op.action)) &&
                created.status !== 'WAITING_CLARIFICATION'
            ) {
                await api.post(`/v1/tasks/${created.task_ref}/run`, undefined, taskSchema)
                await refresh(created)
            }
        } catch (e) {
            setError(e)
            if (!created && e instanceof ApiError && (e.status === 0 || e.status >= 500)) {
                setUncertain(true)
            } else if (!uncertain) pending.current = null
        } finally {
            lock.current = false
            setBusy(false)
        }
    }
    const content = (
        <>
            <form onSubmit={submit}>
                <Notice>先生成计划。只读任务会查询执行；资金和服务变更需在预览后逐项确认。</Notice>
                {inline && (
                    <div className="goal-examples">
                        {[
                            '给小王转账 300 元，至少保留 1000 元',
                            '查询上个月账单',
                            '查询当前余额',
                        ].map((example) => (
                            <button
                                type="button"
                                className="secondary"
                                key={example}
                                disabled={busy || uncertain}
                                onClick={() => setMessage(example)}
                            >
                                {example}
                            </button>
                        ))}
                    </div>
                )}
                {!preset && (
                    <div className="segmented" aria-label="新建方式">
                        {[
                            ...(!task ? [['natural', '自然语言']] : []),
                            ...(!task || prior.length <= 1 ? [['transfer', '转账表单']] : []),
                            ['structured', '完整步骤'],
                        ].map(([v, l]) => (
                            <button
                                key={v}
                                type="button"
                                className={mode === v ? 'active' : ''}
                                disabled={uncertain || busy}
                                onClick={() => {
                                    setMode(v)
                                    pending.current = null
                                }}
                            >
                                {l}
                            </button>
                        ))}
                    </div>
                )}
                <fieldset disabled={busy || uncertain}>
                    {!['transfer', 'preset'].includes(mode) && (
                        <Field label="原始目标">
                            <textarea
                                rows={3}
                                value={message}
                                onChange={(e) => setMessage(e.target.value)}
                                placeholder="例如：给小王转300元，至少保留1000元"
                                required
                            />
                        </Field>
                    )}
                    {mode === 'natural' && !inline && (
                        <div className="example-buttons">
                            {[
                                '查一下余额',
                                '给小王转300元，至少保留1000元',
                                '看看我上个月的账单',
                            ].map((s) => (
                                <button type="button" key={s} onClick={() => setMessage(s)}>
                                    {s}
                                </button>
                            ))}
                        </div>
                    )}
                    {mode === 'transfer' && (
                        <div className="form-grid">
                            <Field label="付款账户">
                                <select
                                    value={account || accounts.data?.[0]?.account_ref || ''}
                                    onChange={(e) => setAccount(e.target.value)}
                                    required
                                >
                                    {accounts.data
                                        ?.filter((a) => a.active)
                                        .map((a) => (
                                            <option key={a.account_ref} value={a.account_ref}>
                                                {a.account_ref}
                                            </option>
                                        ))}
                                </select>
                            </Field>
                            <Field label="已确认收款人">
                                <select
                                    value={
                                        recipient ||
                                        recipients.data?.find((x) => x.active)?.recipient_ref ||
                                        ''
                                    }
                                    onChange={(e) => setRecipient(e.target.value)}
                                    required
                                >
                                    {recipients.data
                                        ?.filter((r) => r.active)
                                        .map((r) => (
                                            <option key={r.recipient_ref} value={r.recipient_ref}>
                                                {r.display_name}
                                            </option>
                                        ))}
                                </select>
                            </Field>
                            <Field label="转账金额（元）">
                                <input
                                    inputMode="decimal"
                                    value={amount}
                                    onChange={(e) => setAmount(e.target.value)}
                                    required
                                />
                            </Field>
                            <Field label="最低保留余额（元）">
                                <input
                                    inputMode="decimal"
                                    value={reserve}
                                    onChange={(e) => setReserve(e.target.value)}
                                    required
                                />
                            </Field>
                        </div>
                    )}
                    {mode === 'preset' && (
                        <div className="preset-summary">
                            {preset?.steps.map((step) => (
                                <div key={step.step_ref}>
                                    <strong>{actionName(step.action)}</strong>
                                    <dl className="facts">
                                        {Object.entries(step.arguments).map(([key, value]) => (
                                            <div className="fact-row" key={key}>
                                                <dt>
                                                    {(
                                                        {
                                                            account_ref: '账户',
                                                            source_account_ref: '付款账户',
                                                            recipient_ref: '收款人',
                                                            amount_minor: '操作金额',
                                                            fee_minor: '费用',
                                                            minimum_remaining_balance_minor:
                                                                '最低保留余额',
                                                            product_ref: '产品',
                                                            card_ref: '卡片',
                                                            locked: '临时锁定',
                                                            subscription_ref: '订阅合同',
                                                            start_date: '开始日期（含）',
                                                            end_date: '结束日期（不含）',
                                                            timezone: '时区',
                                                            category: '分类',
                                                            currency: '币种',
                                                            days: '最近天数',
                                                        } as Record<string, string>
                                                    )[key] ?? key}
                                                </dt>
                                                <dd>
                                                    {key.endsWith('_minor') ? (
                                                        <Money value={value} />
                                                    ) : typeof value === 'boolean' ? (
                                                        value ? (
                                                            '是'
                                                        ) : (
                                                            '否'
                                                        )
                                                    ) : key === 'recipient_ref' ? (
                                                        (recipients.data?.find(
                                                            (r) => r.recipient_ref === value,
                                                        )?.display_name ?? String(value))
                                                    ) : (
                                                        String(value)
                                                    )}
                                                </dd>
                                            </div>
                                        ))}
                                    </dl>
                                </div>
                            ))}
                        </div>
                    )}
                    {mode === 'structured' && (
                        <>
                            <Field
                                label="完整目标步骤 JSON"
                                hint="包含全部节点、依赖及资金到账条件。已完成或待核实操作须原样保留。"
                            >
                                <textarea
                                    className="code-input"
                                    rows={13}
                                    spellCheck={false}
                                    value={script}
                                    onChange={(e) => setScript(e.target.value)}
                                />
                            </Field>
                        </>
                    )}
                    {(mode === 'structured' || mode === 'preset' || !!task) && (
                        <label className="check">
                            <input
                                type="checkbox"
                                checked={reviewed}
                                onChange={(e) => setReviewed(e.target.checked)}
                            />
                            我已逐项核对全部目标、参数与依赖
                        </label>
                    )}
                </fieldset>
                <ErrorNotice error={error} />
                {uncertain && (
                    <Notice kind="warning">
                        响应中断。已保留原请求编号和完整内容；请核对原请求，避免另建重复任务。关闭页面将失去本次恢复信息。
                    </Notice>
                )}
                <div className="actions">
                    {!inline && (
                        <button type="button" className="secondary" onClick={onClose}>
                            返回
                        </button>
                    )}
                    <button
                        className="primary"
                        disabled={
                            busy ||
                            (!uncertain &&
                                (mode === 'structured' || mode === 'preset' || !!task) &&
                                !reviewed)
                        }
                    >
                        {busy
                            ? '正在处理…'
                            : uncertain
                              ? '核对原请求'
                              : task
                                ? '提交完整计划'
                                : '生成任务计划'}
                        <ArrowRight size={16} />
                    </button>
                </div>
            </form>
        </>
    )
    return inline ? (
        <section className="goal-composer" aria-label="新建金融任务">
            {content}
        </section>
    ) : (
        <Modal title={task ? '补全或修改完整计划' : '新建金融任务'} onClose={onClose}>
            {content}
        </Modal>
    )
}

export function TaskDetail({ reference }: { reference: string }) {
    const detail = useTask(reference),
        { api, me } = useSession(),
        refresh = useRefresh(),
        navigate = useNavigate(),
        q = useQueryClient(),
        [activityOpen, setActivityOpen] = useState(false),
        [reportOpen, setReportOpen] = useState(false),
        [selection, setSelection] = useState(''),
        [editing, setEditing] = useState(false),
        [stopping, setStopping] = useState(false),
        [error, setError] = useState<unknown>()
    const reportTrigger = useRef<HTMLButtonElement>(null),
        closeReport = () => {
            setReportOpen(false)
            requestAnimationFrame(() => reportTrigger.current?.focus())
        }
    const task = detail.data,
        ops = task ? activeOperations(task) : [],
        op =
            ops.find((o) => o.operation_ref === selection) ??
            ops.find(
                (o) => ['WAITING_APPROVAL', 'PREPARING'].includes(o.status) && isWrite(o.action),
            ) ??
            ops[0]
    const activity = useQuery({
        queryKey: [me?.subject_ref, 'task-activity', reference],
        enabled: !!api && activityOpen,
        queryFn: ({ signal }) =>
            api!.get(
                `/v1/tasks/${encodeURIComponent(reference)}/trace?limit=200&after=0`,
                z.array(eventSchema),
                signal,
            ),
    })
    const action = useMutation({
        mutationFn: async (kind: 'stop' | 'run' | 'followup' | 'export') => {
            if (!api || !task) return
            if (kind === 'export') {
                const record = await api.get(`/v1/export/${reference}`, objectSchema)
                const blob = new Blob([json(record, true)], { type: 'application/json' }),
                    url = URL.createObjectURL(blob)
                const a = document.createElement('a')
                a.href = url
                a.download = `finguard-${reference}.json`
                a.click()
                setTimeout(() => URL.revokeObjectURL(url), 1000)
                return
            }
            if (kind === 'followup') {
                const next = await api.post(
                    '/v1/tasks',
                    {
                        client_request_ref: newRef(),
                        user_request: '还剩多少',
                        context_task_ref: reference,
                    },
                    taskSchema,
                )
                navigate('/tasks/' + next.task_ref)
                if (next.status !== 'WAITING_CLARIFICATION')
                    await api.post(`/v1/tasks/${next.task_ref}/run`, undefined, taskSchema)
                await refresh(next)
                return
            }
            const next = await api.post(`/v1/tasks/${reference}/${kind}`, undefined, taskSchema)
            q.setQueryData([me?.subject_ref, 'task', reference], next)
            await refresh(next)
        },
        onError: setError,
        onSuccess: () => setStopping(false),
    })
    useEffect(() => {
        if (task?.status === 'COMPLETED') {
            void q.invalidateQueries({ queryKey: [me?.subject_ref, 'accounts'] })
        }
    }, [task?.status, q, me?.subject_ref])
    if (detail.isLoading)
        return (
            <section className="panel">
                <Loading />
            </section>
        )
    if (!task)
        return (
            <section className="panel">
                <ErrorNotice error={detail.error} />
                <button onClick={() => void detail.refetch()} className="secondary">
                    重新读取
                </button>
            </section>
        )
    return (
        <div className={`task-work ${reportOpen ? 'report-open' : ''}`}>
            <section className="task-main">
                <div className="task-heading">
                    <div>
                        <span className="eyebrow">TASK / {reference.slice(-7).toUpperCase()}</span>
                        <div className="user-goal">
                            <span>你的目标</span>
                            <h2>{task.user_request}</h2>
                        </div>
                        <div className="inline">
                            <Badge status={verified(task) ? 'success' : task.status}>
                                {taskLabel(task)}
                            </Badge>
                            <span className="muted">计划版本 {task.current_version}</span>
                        </div>
                    </div>
                    <button
                        className="icon-button"
                        onClick={() => void detail.refetch()}
                        aria-label="刷新任务"
                    >
                        <RefreshCw size={17} />
                    </button>
                </div>
                <Updated at={detail.dataUpdatedAt} stale={!!detail.error} />
                <ErrorNotice error={detail.error ?? error} />
                {task.stop_requested && (
                    <Notice kind="warning">
                        已请求停止后续步骤。已提交操作继续核实，已完成事实保留。
                    </Notice>
                )}
                {task.status === 'WAITING_CLARIFICATION' && (
                    <div className="clarification">
                        <h3>请补充完整目标</h3>
                        {task.questions.map((a, i) => (
                            <p key={i}>{a.message}</p>
                        ))}
                        <button className="secondary" onClick={() => setEditing(true)}>
                            补全结构化计划
                        </button>
                    </div>
                )}
                <details className="plan-disclosure">
                    <summary>查看计划 · {ops.length} 个业务操作</summary>
                    <div className="section-heading">
                        <h3>计划与执行</h3>
                        <span>{ops.length} 个业务操作</span>
                    </div>
                    <div className="plan-steps">
                        {task.plans.at(-1)?.steps.map((step, index) => {
                            const item = ops.find((o) => o.operation_ref === step.operation_ref)
                            if (!item) return null
                            return (
                                <button
                                    key={item.operation_ref}
                                    className={`plan-step ${op?.operation_ref === item.operation_ref ? 'selected' : ''}`}
                                    onClick={() => setSelection(item.operation_ref)}
                                >
                                    <span
                                        className={`step-number ${item.status === 'SUCCEEDED' ? 'done' : ''}`}
                                    >
                                        {item.status === 'SUCCEEDED' ? (
                                            <Check size={16} />
                                        ) : (
                                            String(index + 1).padStart(2, '0')
                                        )}
                                    </span>
                                    <div>
                                        <div className="step-title">
                                            <strong>{actionName(item.action)}</strong>
                                            <Badge status={item.status}>
                                                {operationLabel(item)}
                                            </Badge>
                                        </div>
                                        <p>
                                            {isMinor(item.arguments.amount_minor) ? (
                                                <Money value={item.arguments.amount_minor} />
                                            ) : (
                                                text(
                                                    item.arguments.account_ref ??
                                                        item.arguments.subscription_ref ??
                                                        item.arguments.card_ref,
                                                    '查看操作详情',
                                                )
                                            )}
                                        </p>
                                        {step.depends_on.length > 0 && (
                                            <small>
                                                前置：{step.depends_on.join('、')}
                                                {step.funds_available_after.length
                                                    ? ' · 需核实资金到账'
                                                    : ''}
                                            </small>
                                        )}
                                        {item.error_code && (
                                            <small className="error-code">{item.error_code}</small>
                                        )}
                                    </div>
                                    <ChevronRight size={16} />
                                </button>
                            )
                        })}
                    </div>
                </details>
                {op && (
                    <div className="inline-review">
                        <span className="eyebrow">FinGuard · 当前行动</span>
                        <OperationInspector key={op.operation_ref} task={task} op={op} />
                    </div>
                )}
                {task.evidence.length > 0 && (
                    <button
                        ref={reportTrigger}
                        className="primary"
                        onClick={() => setReportOpen(true)}
                    >
                        打开报告与凭证
                    </button>
                )}
                {!ops.length && task.status !== 'WAITING_CLARIFICATION' && (
                    <Empty title="尚无可展示的操作">请读取最新任务状态。</Empty>
                )}
                {verified(task) && (
                    <div className="result-banner">
                        <CheckCircle2 size={28} />
                        <div>
                            <strong>任务已完成，业务结果已核实</strong>
                            <p>本计划的操作均已成功，并关联业务证据。</p>
                        </div>
                    </div>
                )}
                {task.status === 'COMPLETED' && !verified(task) && (
                    <Notice kind="warning">
                        完成状态待核验。当前证据尚不满足成功展示条件，请刷新任务详情。
                    </Notice>
                )}
                <div className="task-actions">
                    {!['COMPLETED', 'TERMINATED', 'WAITING_CLARIFICATION'].includes(
                        task.status,
                    ) && (
                        <>
                            <button
                                className="secondary"
                                disabled={action.isPending}
                                onClick={() => setStopping(true)}
                            >
                                <Square size={14} />
                                停止后续步骤
                            </button>
                            {ops.some(editable) && (
                                <button className="text-button" onClick={() => setEditing(true)}>
                                    修改未提交步骤
                                </button>
                            )}
                            <button
                                className="text-button"
                                disabled={action.isPending}
                                onClick={() => action.mutate('run')}
                            >
                                {ops.some((o) => o.status === 'UNKNOWN')
                                    ? '核实原操作'
                                    : '推进原任务'}
                            </button>
                        </>
                    )}
                    {ops.length > 0 && (
                        <button
                            className="text-button"
                            disabled={action.isPending}
                            onClick={() => action.mutate('followup')}
                        >
                            关联查询：还剩多少
                            <ArrowRight size={14} />
                        </button>
                    )}
                </div>
                <details
                    className="activity"
                    onToggle={(e) => {
                        const open = e.currentTarget.open
                        setActivityOpen(open)
                        if (open) void activity.refetch()
                    }}
                >
                    <summary>查看执行过程</summary>
                    <p className="muted">服务端记录 · 最多前 200 条；完整分页可进入诊断。</p>
                    <ErrorNotice error={activity.error} />
                    {activity.isFetching && <Loading />}
                    <ol>
                        {activity.data?.map((event) => (
                            <li key={event.sequence}>
                                <strong>{event.kind}</strong>
                                <small> · {time(event.occurred_at)}</small>
                            </li>
                        ))}
                    </ol>
                </details>
                <details className="detail-note">
                    <summary>任务关联与原始目标</summary>
                    <dl className="facts">
                        <dt>任务编号</dt>
                        <dd>{task.task_ref}</dd>
                        <dt>创建时间</dt>
                        <dd>{time(task.created_at)}</dd>
                        <dt>关联任务</dt>
                        <dd>{task.context_task_ref ?? '无'}</dd>
                    </dl>
                </details>
                <div className="export-row">
                    <button
                        className="text-button"
                        disabled={action.isPending}
                        onClick={() => action.mutate('export')}
                    >
                        <ArrowDownLeft size={15} />
                        导出任务记录
                    </button>
                    <small>有限脱敏 · 最多首批 1000 条轨迹 · 分享前请检查</small>
                    {me?.scopes.includes('tasks.read') && (
                        <Link to={'/lab/tasks/' + reference} className="text-link">
                            查看诊断
                        </Link>
                    )}
                </div>
            </section>
            {reportOpen && (
                <aside className="report-panel" aria-label="报告与凭证">
                    <div className="report-heading">
                        <h2>报告与凭证</h2>
                        <button className="secondary" onClick={closeReport}>
                            关闭报告
                        </button>
                    </div>
                    <p>任务：{task.user_request}</p>
                    {ops
                        .filter(
                            (item) =>
                                item.status === 'SUCCEEDED' &&
                                task.evidence.some((e) => e.operation_ref === item.operation_ref),
                        )
                        .map((item) => (
                            <section className="panel" key={item.operation_ref}>
                                <h3>{actionName(item.action)}</h3>
                                {isMinor(item.arguments.amount_minor) && (
                                    <div className="report-amount">
                                        <span>操作金额</span>
                                        <Money value={item.arguments.amount_minor} large />
                                    </div>
                                )}
                                <ResultSummary op={item} />
                                <Raw title="业务结果" value={item.result} />
                                {task.evidence
                                    .filter((e) => e.operation_ref === item.operation_ref)
                                    .map((e) => (
                                        <dl className="facts" key={e.evidence_ref}>
                                            <dt>证据编号</dt>
                                            <dd>{e.evidence_ref}</dd>
                                            <dt>来源</dt>
                                            <dd>{e.source}</dd>
                                            <dt>业务记录</dt>
                                            <dd>{e.record_ref}</dd>
                                        </dl>
                                    ))}
                            </section>
                        ))}
                    {!verified(task) && (
                        <Notice kind="warning">
                            任务尚未全部核实。这里只展示已有的已核实结果。
                        </Notice>
                    )}
                </aside>
            )}
            {editing && <Composer task={task} onClose={() => setEditing(false)} />}{' '}
            {stopping && (
                <Confirm
                    title="停止后续步骤？"
                    description={stopMessage()}
                    onClose={() => setStopping(false)}
                    onConfirm={() => action.mutate('stop')}
                    busy={action.isPending}
                />
            )}
        </div>
    )
}

function OperationInspector({ task, op }: { task: Task; op: Operation }) {
    const { api, me } = useSession(),
        q = useQueryClient(),
        refresh = useRefresh(),
        [error, setError] = useState<unknown>(),
        [busy, setBusy] = useState(false),
        [conflict, setConflict] = useState<{ old: Preview; next: Preview } | null>(null),
        [ack, setAck] = useState(false),
        [uncertain, setUncertain] = useState(false),
        [message, setMessage] = useState(''),
        [grantRef, setGrantRef] = useState(''),
        lock = useRef(false)
    const recipients = useRecipients()
    const previewKey = [
        me?.subject_ref,
        'preview',
        op.operation_ref,
        op.digest,
        task.current_version,
    ]
    const preview = useQuery({
        queryKey: previewKey,
        queryFn: () => api!.preview(op.operation_ref),
        enabled: !!api && editable(op),
        retry: false,
    })
    const approvals = useQuery({
        queryKey: [me?.subject_ref, 'approvals', op.operation_ref],
        queryFn: () =>
            api!.get(`/v1/operations/${op.operation_ref}/approvals`, z.array(approvalSchema)),
        enabled: !!api && isWrite(op.action),
        retry: false,
    })
    const grants = useQuery({
        queryKey: [me?.subject_ref, 'delegations', 'operation'],
        enabled:
            !!api &&
            op.action === 'transfer.create' &&
            editable(op) &&
            !!me?.scopes.includes('approvals.write'),
        queryFn: () => api!.get('/v1/delegations?limit=50', z.array(delegationSchema)),
    })
    useEffect(() => {
        setConflict(null)
        setAck(false)
        setError(null)
        setUncertain(false)
        setMessage('')
        setGrantRef('')
    }, [op.operation_ref, op.digest])
    const confirm = async () => {
        if (lock.current || !api || !preview.data) return
        lock.current = true
        setBusy(true)
        setError(null)
        setMessage('')
        let writing = false
        try {
            const shown = preview.data
            const latest = await api.task(task.task_ref)
            const next = await api.preview(op.operation_ref)
            const current = activeOperations(latest).find(
                (o) => o.operation_ref === op.operation_ref,
            )
            if (
                !current ||
                !editable(current) ||
                latest.current_version !== task.current_version ||
                next.digest !== shown.digest ||
                canonical(next.preview) !== canonical(shown.preview) ||
                canonical(next.authentication_policy) !== canonical(shown.authentication_policy)
            ) {
                setConflict({ old: shown, next })
                setAck(false)
                q.setQueryData(previewKey, next)
                q.setQueryData([me?.subject_ref, 'task', task.task_ref], latest)
                throw new Error('预览或计划已变化，请核对新旧参数，再次明确确认。')
            }
            if (next.authentication_policy.step_up_required)
                throw new Error('此操作需要额外验证，当前验证服务尚未接入。')
            writing = true
            if (grantRef)
                await api.post(
                    `/v1/operations/${op.operation_ref}/delegation`,
                    { delegation_ref: grantRef },
                    objectSchema,
                )
            else
                await api.post(
                    `/v1/operations/${op.operation_ref}/approvals`,
                    { expected_digest: shown.digest },
                    objectSchema,
                )
            const updated = await api.post(`/v1/tasks/${task.task_ref}/run`, undefined, taskSchema)
            q.setQueryData([me?.subject_ref, 'task', task.task_ref], updated)
            await refresh(updated)
            void approvals.refetch()
            setMessage('已收到服务端执行状态，结果以业务凭证为准。')
        } catch (e) {
            setError(e)
            if (writing && e instanceof ApiError && (e.status === 0 || e.status >= 500))
                setUncertain(true)
            if (e instanceof ApiError && e.status === 409) {
                void preview.refetch()
                void refresh(task)
            }
        } finally {
            lock.current = false
            setBusy(false)
        }
    }
    const inspect = async () => {
        setError(null)
        setBusy(true)
        try {
            await Promise.all([approvals.refetch(), refresh(task), preview.refetch()])
            setMessage('已读取原授权与任务状态。请核对记录；如授权有效，可推进原任务。')
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }
    const revoke = async (ref: string) => {
        if (!api || lock.current) return
        lock.current = true
        setBusy(true)
        try {
            await api.post(`/v1/approvals/${ref}/revoke`, undefined, objectSchema)
            await refresh(task)
            void approvals.refetch()
        } catch (e) {
            setError(e)
        } finally {
            lock.current = false
            setBusy(false)
        }
    }
    const args = op.arguments,
        p = editable(op) ? preview.data?.preview : undefined,
        blocked = editable(op) && preview.data?.authentication_policy.step_up_required,
        proof = task.evidence.filter((e) => e.operation_ref === op.operation_ref)
    return (
        <Panel
            title={
                op.status === 'SUCCEEDED'
                    ? '结果凭证'
                    : isWrite(op.action) && editable(op)
                      ? '确认本次操作'
                      : '查询详情'
            }
            tag={op.status === 'SUCCEEDED' ? 'EVIDENCE' : 'REVIEW & CONFIRM'}
        >
            <div className="operation-kind">
                <ShieldCheck size={19} />
                {actionName(op.action)}
            </div>
            {preview.isLoading && <Loading />}
            <ErrorNotice error={preview.error ?? error} />
            {isMinor(args.amount_minor) && (
                <div className="amount-display">
                    <span>{op.action === 'transfer.create' ? '转账金额' : '操作金额'}</span>
                    <Money value={args.amount_minor} large />
                </div>
            )}
            <dl className="facts">
                {op.action === 'transfer.create' && (
                    <>
                        <dt>收款人</dt>
                        <dd>
                            {text(
                                preview.data?.preview.recipient_name,
                                recipients.data?.find((r) => r.recipient_ref === args.recipient_ref)
                                    ?.display_name ?? (args.recipient_ref as string),
                            )}
                        </dd>
                        <dt>付款账户</dt>
                        <dd>{text(args.source_account_ref)}</dd>
                    </>
                )}
                {args.account_ref !== undefined && (
                    <>
                        <dt>账户</dt>
                        <dd>{text(args.account_ref)}</dd>
                    </>
                )}
                {args.product_ref !== undefined && (
                    <>
                        <dt>模拟产品</dt>
                        <dd>{text(args.product_ref)}</dd>
                    </>
                )}
                {isMinor(args.fee_minor) && (
                    <>
                        <dt>手续费</dt>
                        <dd>
                            <Money value={args.fee_minor} />
                        </dd>
                    </>
                )}
                {p?.available_balance_minor !== undefined && (
                    <>
                        <dt>当前可用余额</dt>
                        <dd>
                            <Money value={p.available_balance_minor} />
                        </dd>
                    </>
                )}
                {op.action === 'transfer.create' &&
                    remaining(p?.available_balance_minor, args.amount_minor, args.fee_minor) !==
                        undefined && (
                        <>
                            <dt>预计执行后余额</dt>
                            <dd>
                                <Money
                                    value={remaining(
                                        p?.available_balance_minor,
                                        args.amount_minor,
                                        args.fee_minor,
                                    )}
                                />
                            </dd>
                        </>
                    )}
                {isMinor(args.minimum_remaining_balance_minor) && (
                    <>
                        <dt>最低保留余额</dt>
                        <dd>
                            <Money value={args.minimum_remaining_balance_minor} />
                        </dd>
                    </>
                )}
                {args.start_date !== undefined && (
                    <>
                        <dt>日期区间</dt>
                        <dd>
                            {text(args.start_date)} 至 {text(args.end_date)}（结束日不含）
                        </dd>
                    </>
                )}
                {args.days !== undefined && (
                    <>
                        <dt>查询范围</dt>
                        <dd>最近 {String(args.days)} 天</dd>
                    </>
                )}
                {args.category !== undefined && (
                    <>
                        <dt>分类</dt>
                        <dd>{text(args.category)}</dd>
                    </>
                )}
                {op.action === 'card.set_lock' && (
                    <>
                        <dt>卡片</dt>
                        <dd>{text(args.card_ref)}</dd>
                        <dt>卡片操作</dt>
                        <dd>{args.locked ? '临时锁定' : '解除临时锁定'}</dd>
                    </>
                )}
                {args.subscription_ref !== undefined && (
                    <>
                        <dt>订阅合同</dt>
                        <dd>{text(args.subscription_ref)}</dd>
                    </>
                )}
            </dl>
            {conflict && (
                <div className="conflict">
                    <h3>请核对更新后的预览</h3>
                    <Raw value={conflict.old.preview} title="变更前" />
                    <Raw value={conflict.next.preview} title="变更后" />
                    <label className="check">
                        <input
                            type="checkbox"
                            checked={ack}
                            onChange={(e) => setAck(e.target.checked)}
                        />
                        我已核对新旧参数
                    </label>
                </div>
            )}
            {blocked && (
                <Notice kind="warning">
                    此操作需要额外验证，当前验证服务尚未接入。可修改未提交步骤，普通确认或委托不能绕过。
                </Notice>
            )}
            {isWrite(op.action) && editable(op) && !uncertain && (
                <>
                    {op.action === 'transfer.create' && !!grants.data?.length && (
                        <Field
                            label="授权方式"
                            hint="仅列出前 50 条已加载委托，范围及预算由服务端复检。"
                        >
                            <select
                                value={grantRef}
                                disabled={busy || !!blocked}
                                onChange={(e) => setGrantRef(e.target.value)}
                            >
                                <option value="">本次单独确认</option>
                                {grants.data
                                    .filter(
                                        (g) => !g.revoked && Date.parse(g.expires_at) > Date.now(),
                                    )
                                    .map((g) => (
                                        <option key={g.delegation_ref} value={g.delegation_ref}>
                                            使用委托 {g.delegation_ref.slice(-8)}
                                        </option>
                                    ))}
                            </select>
                        </Field>
                    )}
                    <div className="approval-scope">
                        <ShieldCheck size={16} />
                        <span>
                            {grantRef
                                ? '将按选定委托绑定本次操作并请求执行，委托的全部限制同时生效。'
                                : '确认仅限本操作与当前参数，有效期默认 5 分钟。'}
                        </span>
                    </div>
                    <button
                        className="primary full"
                        onClick={() => void confirm()}
                        disabled={
                            busy ||
                            !preview.data ||
                            !!preview.error ||
                            !!blocked ||
                            (!!conflict && !ack) ||
                            !me?.scopes.includes('approvals.write')
                        }
                    >
                        {busy
                            ? '正在确认并请求执行…'
                            : op.action === 'transfer.create'
                              ? `${grantRef ? '按委托转账' : '确认转账'} ${money(args.amount_minor)}`
                              : '确认操作并请求执行'}
                        <ArrowRight size={16} />
                    </button>
                    <small className="approval-note">
                        确认后将请求执行；实际余额以提交时复检及结果凭证为准。
                    </small>
                </>
            )}
            {uncertain && (
                <>
                    <Notice kind="warning">
                        请求响应中断，请先核对原授权和原任务。不会自动重新签发授权。
                    </Notice>
                    <button
                        className="secondary full"
                        disabled={busy}
                        onClick={() => void inspect()}
                    >
                        检查原授权与任务状态
                    </button>
                    <button
                        className="text-button"
                        disabled={busy}
                        onClick={() => {
                            setUncertain(false)
                            setMessage('请再次核对预览与授权记录后决定是否确认。')
                        }}
                    >
                        已核对，返回预览
                    </button>
                </>
            )}
            {message && (
                <p role="status" className="muted">
                    {message}
                </p>
            )}
            {op.status === 'UNKNOWN' && (
                <Notice>结果待核实。请刷新原任务，不要重复创建付款。</Notice>
            )}
            {op.status === 'SUCCEEDED' && proof.length > 0 && (
                <>
                    <VerifiedMark />
                    <ResultSummary op={op} />
                    <div className="proof-list">
                        {proof.map((e) => (
                            <details key={e.evidence_ref}>
                                <summary>
                                    {e.kind === 'business_record'
                                        ? '业务记录'
                                        : e.kind === 'ledger'
                                          ? '交易流水'
                                          : '业务证据'}
                                    <span>{time(e.verified_at)}</span>
                                </summary>
                                <dl className="facts">
                                    <dt>来源</dt>
                                    <dd>{e.source}</dd>
                                    <dt>业务记录</dt>
                                    <dd>{e.record_ref}</dd>
                                    <dt>操作</dt>
                                    <dd>{e.operation_ref}</dd>
                                </dl>
                            </details>
                        ))}
                    </div>
                </>
            )}
            {approvals.data?.length ? (
                <details className="approval-records">
                    <summary>本操作的授权记录（{approvals.data.length}）</summary>
                    {approvals.data.map((a) => (
                        <div key={a.approval_ref}>
                            <Badge status={a.status === 'ACTIVE' ? 'warning' : 'neutral'}>
                                {a.status}
                            </Badge>
                            <small>到期 {time(a.expires_at)}</small>
                            {a.status === 'ACTIVE' && (
                                <button
                                    className="text-button"
                                    disabled={busy}
                                    onClick={() => void revoke(a.approval_ref)}
                                >
                                    撤销尚未消费的授权
                                </button>
                            )}
                        </div>
                    ))}
                </details>
            ) : null}
            {preview.data && (
                <Raw
                    value={preview.data}
                    title={editable(op) ? '技术参数与摘要' : '执行前预览快照（非当前余额）'}
                />
            )}
        </Panel>
    )
}

export function ResultSummary({ op }: { op: Operation }) {
    const result = op.result ?? {}
    return (
        <div className="result-summary">
            {isMinor(result.balance_after_minor ?? result.balance_minor) && (
                <div>
                    <span>执行时余额</span>
                    <Money value={result.balance_after_minor ?? result.balance_minor} large />
                </div>
            )}
            {op.action === 'bills.analyze' && (
                <>
                    <dl className="facts">
                        <dt>支出</dt>
                        <dd>
                            <Money value={result.spending_minor} />
                        </dd>
                        <dt>退款</dt>
                        <dd>
                            <Money value={result.refunds_minor} />
                        </dd>
                        <dt>净支出</dt>
                        <dd>
                            <Money value={result.net_spending_minor} />
                        </dd>
                    </dl>
                    <CategoryBars categories={result.categories} />
                    <small>已入账现金流，排除内部转账与理财流动。</small>
                </>
            )}
            {op.action === 'subscription.cancel' && (
                <Notice>订阅合同已取消，退款为零；代扣授权未变更。</Notice>
            )}
            {op.action === 'card.set_lock' && (
                <p>
                    卡片状态：
                    {result.state === 'LOCKED'
                        ? '临时锁定'
                        : result.state === 'ACTIVE'
                          ? '正常'
                          : '待核实'}
                    。
                </p>
            )}
            {op.action === 'product.redeem' && (
                <p>
                    {result.funds_available === true
                        ? '赎回资金已到账并核实。'
                        : '赎回受理情况以业务证据为准。'}
                </p>
            )}
            {op.action === 'product.subscribe' && <p>模拟产品申购结果已核实，未提供收益预测。</p>}
        </div>
    )
}
export function CategoryBars({ categories }: { categories: unknown }) {
    if (!categories || typeof categories !== 'object') return null
    const entries = Object.entries(categories).filter((e): e is [string, number | bigint] =>
        isMinor(e[1]),
    )
    const max = entries.reduce((a, [, v]) => (BigInt(v) > a ? BigInt(v) : a), 1n)
    return (
        <div className="category-bars">
            {entries.map(([k, v]) => (
                <div key={k}>
                    <div>
                        <span>
                            {{
                                food: '餐饮',
                                transport: '交通',
                                shopping: '购物',
                                groceries: '日常采购',
                                subscription: '订阅',
                            }[k] ?? k}
                        </span>
                        <Money value={v} />
                    </div>
                    <span className="bar-track">
                        <span
                            style={{
                                width: `${BigInt(v) > 0 ? Number((BigInt(v) * 100n) / max) : 0}%`,
                            }}
                        />
                    </span>
                </div>
            ))}
        </div>
    )
}
