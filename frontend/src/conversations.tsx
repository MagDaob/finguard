import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams, useLocation } from 'react-router-dom'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { newRef, ApiError } from './api'
import { useSession } from './session'
import { useRefresh, useTask } from './hooks'
import { activeOperations, taskLabel, verified } from './state'
import { ErrorNotice, Loading, Badge } from './ui'
import { TaskDetail } from './tasks'
const rowSchema = z.object({
    conversation_ref: z.string(),
    title: z.string(),
    created_at: z.string(),
})
const conversationSchema = z.object({
    conversation_ref: z.string(),
    turns: z.array(
        z.object({
            sequence: z.number().int(),
            message_ref: z.string(),
            user_text: z.string(),
            task_ref: z.string().nullable(),
            ready: z.boolean(),
            reply: z.string(),
            created_at: z.string(),
        }),
    ),
})
type Pending = { message_ref: string; content: string; expected_sequence: number }
export function ConversationHistory() {
    const { api, me } = useSession(),
        { conversationRef } = useParams()
    const rows = useInfiniteQuery({
        queryKey: [me?.subject_ref, 'conversations'],
        enabled: !!api,
        initialPageParam: '',
        queryFn: ({ pageParam, signal }) =>
            api!.get(
                '/v1/conversations' + (pageParam ? '?before=' + encodeURIComponent(pageParam) : ''),
                z.array(rowSchema),
                signal,
            ),
        getNextPageParam: (last) =>
            last.length === 25 ? last.at(-1)?.conversation_ref : undefined,
    })
    return (
        <section className="conversation-history" aria-label="对话历史">
            <h2>最近对话</h2>
            <ErrorNotice error={rows.error} />
            {rows.isLoading && <Loading />}
            {rows.data?.pages.flat().map((row) => (
                <Link
                    className={row.conversation_ref === conversationRef ? 'selected' : ''}
                    to={'/tasks/chat/' + row.conversation_ref}
                    key={row.conversation_ref}
                >
                    {row.title}
                </Link>
            ))}
            {rows.hasNextPage && (
                <button
                    disabled={rows.isFetchingNextPage}
                    onClick={() => void rows.fetchNextPage()}
                >
                    加载更多对话
                </button>
            )}
        </section>
    )
}
function TaskRow({ reference, onSelect }: { reference: string; onSelect: () => void }) {
    const task = useTask(reference)
    return (
        <tr>
            <td>
                <button className="text-button" onClick={onSelect}>
                    {task.data?.user_request ?? reference}
                </button>
            </td>
            <td>
                {task.data ? (
                    <Badge status={verified(task.data) ? 'success' : task.data.status}>
                        {taskLabel(task.data)}
                    </Badge>
                ) : task.isError ? (
                    '读取失败'
                ) : (
                    '读取中'
                )}
            </td>
            <td>{task.data ? activeOperations(task.data).length : '—'}</td>
        </tr>
    )
}
export function ConversationRoute() {
    const { conversationRef } = useParams(),
        location = useLocation()
    return <ConversationPage key={conversationRef ?? location.key} reference={conversationRef} />
}
function ConversationPage({ reference }: { reference?: string }) {
    const { api, me } = useSession(),
        navigate = useNavigate(),
        q = useQueryClient(),
        refresh = useRefresh()
    const [draft, setDraft] = useState(''),
        [busy, setBusy] = useState(false),
        [error, setError] = useState<unknown>(),
        [selected, setSelected] = useState('')
    const id = useRef(reference ?? newRef()),
        pending = useRef<Pending | null>(null),
        lock = useRef(false),
        input = useRef<HTMLTextAreaElement>(null)
    const query = useQuery({
        queryKey: [me?.subject_ref, 'conversation', reference],
        enabled: !!api && !!reference,
        queryFn: ({ signal }) =>
            api!.get('/v1/conversations/' + reference, conversationSchema, signal),
        refetchInterval: (query) => (query.state.data?.turns.some((t) => !t.ready) ? 3000 : false),
    })
    const conversation = query.data,
        turns = conversation?.turns ?? [],
        taskRefs = [...new Set(turns.flatMap((t) => (t.task_ref ? [t.task_ref] : [])))],
        lastTask = taskRefs.at(-1),
        active = selected || lastTask
    useEffect(() => {
        setSelected('')
    }, [lastTask])
    async function send(e?: FormEvent, recovery?: Pending) {
        e?.preventDefault()
        if (!api || lock.current) return
        const body = pending.current ??
            recovery ?? {
                message_ref: newRef(),
                content: draft.trim(),
                expected_sequence: turns.at(-1)?.sequence ?? 0,
            }
        if (!body.content) return
        pending.current = body
        lock.current = true
        setBusy(true)
        setError(null)
        try {
            const next = await api.post(
                '/v1/conversations/' + id.current + '/messages',
                body,
                conversationSchema,
            )
            pending.current = null
            setDraft('')
            q.setQueryData([me?.subject_ref, 'conversation', id.current], next)
            await q.invalidateQueries({ queryKey: [me?.subject_ref, 'conversations'] })
            for (const ref of [
                ...new Set(next.turns.flatMap((t) => (t.task_ref ? [t.task_ref] : []))),
            ])
                await q.invalidateQueries({ queryKey: [me?.subject_ref, 'task', ref] })
            await refresh()
            if (!reference) navigate('/tasks/chat/' + id.current, { replace: true })
            else await query.refetch()
            input.current?.focus()
        } catch (e) {
            if (
                e instanceof ApiError &&
                e.status >= 400 &&
                e.status < 500 &&
                !['CONVERSATION_BUSY', 'MESSAGE_PENDING'].includes(e.code)
            ) {
                pending.current = null
                if (reference) void query.refetch()
            }
            setError(e)
        } finally {
            lock.current = false
            setBusy(false)
        }
    }
    const unfinished = turns.find((t) => !t.ready)
    return (
        <section className="chat-page">
            <header className="chat-heading">
                <div>
                    <span className="eyebrow">FinGuard AGENT</span>
                    <h1>{reference ? '任务对话' : '开始对话'}</h1>
                    <p>描述目标，自动登记任务；查询自动推进，资金操作由你确认。</p>
                </div>
                <Link to="/tasks" className="secondary">
                    新对话
                </Link>
            </header>
            <ErrorNotice error={query.error} />
            {query.isLoading && <Loading />}
            {!turns.length && !query.isLoading && (
                <div className="chat-welcome">
                    <h2>今天想完成什么？</h2>
                    <div className="goal-examples">
                        {['查询当前余额', '看看上个月账单，再给小王转300元，至少保留1000元'].map(
                            (example) => (
                                <button
                                    className="secondary"
                                    key={example}
                                    onClick={() => {
                                        setDraft(example)
                                        input.current?.focus()
                                    }}
                                >
                                    {example}
                                </button>
                            ),
                        )}
                    </div>
                </div>
            )}
            <div className="chat-transcript" aria-label="对话记录">
                {turns.map((turn) => (
                    <article className="chat-turn" key={turn.message_ref}>
                        <div className="chat-user">
                            <span>你</span>
                            <p>{turn.user_text}</p>
                        </div>
                        <div className="chat-agent">
                            <strong>FinGuard</strong>
                            <p>{turn.ready ? turn.reply : '正在处理原消息，请核对或恢复。'}</p>
                            {turn.task_ref && (
                                <button
                                    className="text-button"
                                    onClick={() => setSelected(turn.task_ref!)}
                                >
                                    查看关联任务
                                </button>
                            )}
                        </div>
                    </article>
                ))}
                {pending.current &&
                    !turns.some((t) => t.message_ref === pending.current?.message_ref) && (
                        <article className="chat-turn">
                            <div className="chat-user">
                                <span>你</span>
                                <p>{pending.current.content}</p>
                            </div>
                            <div className="chat-agent" role="status">
                                {busy
                                    ? '正在识别目标并核实任务状态…'
                                    : '原消息结果待核实，请恢复原消息。'}
                            </div>
                        </article>
                    )}
            </div>
            {taskRefs.length > 0 && (
                <details className="conversation-task-table">
                    <summary>任务表 · {taskRefs.length} 项任务</summary>
                    <div className="table-scroll">
                        <table>
                            <thead>
                                <tr>
                                    <th>任务</th>
                                    <th>状态</th>
                                    <th>当前业务操作</th>
                                </tr>
                            </thead>
                            <tbody>
                                {taskRefs.map((ref) => (
                                    <TaskRow
                                        key={ref}
                                        reference={ref}
                                        onSelect={() => setSelected(ref)}
                                    />
                                ))}
                            </tbody>
                        </table>
                    </div>
                </details>
            )}
            {active && (
                <section aria-label="当前任务" className="conversation-active">
                    <TaskDetail key={active} reference={active} embedded />
                </section>
            )}
            <form className="chat-composer" onSubmit={(e) => void send(e)}>
                <label htmlFor="chat-message">{reference ? '继续对话' : '你的目标'}</label>
                <textarea
                    id="chat-message"
                    ref={input}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    disabled={busy || !!pending.current || !!unfinished}
                    placeholder="输入目标，或补充信息。例如：改成200元、还剩多少"
                    rows={3}
                    maxLength={4000}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                            e.preventDefault()
                            void send()
                        }
                    }}
                />
                <ErrorNotice error={error} />
                <div className="actions">
                    <small>Enter 发送 · Shift + Enter 换行</small>
                    {pending.current || unfinished ? (
                        <button
                            className="primary"
                            type="button"
                            disabled={busy}
                            onClick={() =>
                                void send(
                                    undefined,
                                    unfinished
                                        ? {
                                              message_ref: unfinished.message_ref,
                                              content: unfinished.user_text,
                                              expected_sequence: unfinished.sequence - 1,
                                          }
                                        : undefined,
                                )
                            }
                        >
                            {busy ? '正在核对…' : '核对并恢复原消息'}
                        </button>
                    ) : (
                        <button
                            className="primary"
                            disabled={busy || !draft.trim() || !me?.scopes.includes('tasks.write')}
                        >
                            {busy ? '正在识别任务…' : '发送目标 →'}
                        </button>
                    )}
                </div>
            </form>
        </section>
    )
}
