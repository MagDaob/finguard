import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { z } from 'zod'
import { FileJson2, RefreshCw, Search, ShieldCheck } from 'lucide-react'
import { eventSchema, objectSchema, text, time, type TraceEvent } from './api'
import { json } from './money'
import { useTask, useVisible } from './hooks'
import { useSession } from './session'
import { Confirm, Empty, ErrorNotice, Field, Notice, Panel, Raw, Updated } from './ui'

export function LabPage() {
    const { api, me } = useSession(),
        location = useLocation(),
        [reference, setReference] = useState(
            location.pathname.startsWith('/lab/tasks/')
                ? location.pathname.slice('/lab/tasks/'.length)
                : '',
        ),
        [input, setInput] = useState(reference),
        [events, setEvents] = useState<TraceEvent[]>([]),
        [error, setError] = useState<unknown>(),
        [busy, setBusy] = useState(false),
        [all, setAll] = useState(false),
        [prompt, setPrompt] = useState(''),
        [candidateRef, setCandidateRef] = useState(''),
        [candidate, setCandidate] = useState<Record<string, unknown> | null>(null),
        [suite, setSuite] = useState(''),
        [report, setReport] = useState<Record<string, unknown> | null>(null),
        [pending, setPending] = useState(''),
        [reviewed, setReviewed] = useState(false)
    const task = useTask(reference),
        visible = useVisible(),
        eventsRef = useRef<TraceEvent[]>([]),
        generation = useRef(0),
        canEvaluate = me?.scopes.includes('evaluation.manage')
    useEffect(() => setReviewed(false), [report, candidateRef, suite])
    const trace = useQuery({
        queryKey: [me?.subject_ref, 'trace-page', reference, events.at(-1)?.sequence ?? 0],
        enabled: !!api && !!reference,
        queryFn: () =>
            api!.get(
                `/v1/tasks/${encodeURIComponent(reference)}/trace?limit=200&after=${eventsRef.current.at(-1)?.sequence ?? 0}`,
                z.array(eventSchema),
            ),
        refetchInterval:
            visible && !['COMPLETED', 'TERMINATED'].includes(task.data?.status ?? '')
                ? 2000
                : false,
    })
    useEffect(() => {
        if (!trace.data) return
        const merged = [
            ...new Map([...eventsRef.current, ...trace.data].map((e) => [e.sequence, e])).values(),
        ].sort((a, b) => a.sequence - b.sequence)
        if (merged.length !== eventsRef.current.length) {
            eventsRef.current = merged
            setEvents(merged)
        }
    }, [trace.data])
    useEffect(() => {
        generation.current++
        eventsRef.current = []
        setEvents([])
        setAll(false)
    }, [reference])
    const loadAll = async () => {
        if (!api || busy) return
        const id = generation.current
        setBusy(true)
        setError(null)
        try {
            let collected = [...eventsRef.current]
            for (let pages = 0; pages < 100; pages++) {
                const chunk = await api.get(
                    `/v1/tasks/${encodeURIComponent(reference)}/trace?limit=200&after=${collected.at(-1)?.sequence ?? 0}`,
                    z.array(eventSchema),
                )
                if (id !== generation.current) return
                if (!chunk.length) {
                    setAll(true)
                    break
                }
                const next = [
                    ...new Map([...collected, ...chunk].map((e) => [e.sequence, e])).values(),
                ].sort((a, b) => a.sequence - b.sequence)
                if (next.length === collected.length) throw new Error('轨迹分页没有进展，已停止。')
                collected = next
                eventsRef.current = collected
                setEvents(collected)
                if (chunk.length < 200) {
                    setAll(true)
                    break
                }
            }
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }
    const evaluation = async (kind: string) => {
        if (!api || busy) return
        setBusy(true)
        setError(null)
        try {
            if (kind === 'create') {
                const c = await api.post('/v1/evaluation/candidates', { prompt }, objectSchema)
                setCandidateRef(text(c.candidate_ref, ''))
                setCandidate(c)
                setReport(null)
            } else if (kind === 'load') {
                const c = await api.get(
                    '/v1/evaluation/candidates/' + encodeURIComponent(candidateRef),
                    objectSchema,
                )
                setCandidate(c)
                setReport(
                    c.evaluation && typeof c.evaluation === 'object'
                        ? (c.evaluation as Record<string, unknown>)
                        : null,
                )
            } else if (kind === 'replay') {
                setReport(await api.post('/v1/evaluation/replays', JSON.parse(suite), objectSchema))
            } else if (kind === 'evaluate') {
                setReport(
                    await api.post(
                        `/v1/evaluation/candidates/${encodeURIComponent(candidateRef)}/evaluate`,
                        JSON.parse(suite),
                        objectSchema,
                    ),
                )
            } else if (kind === 'release') {
                if (
                    !reviewed ||
                    !report?.eligible_for_release ||
                    typeof report.evaluation_digest !== 'string'
                )
                    throw new Error('请先取得允许发布的评估结果，并明确审核。')
                setCandidate(
                    await api.post(
                        `/v1/evaluation/candidates/${encodeURIComponent(candidateRef)}/release`,
                        { expected_evaluation_digest: report.evaluation_digest, reviewed: true },
                        objectSchema,
                    ),
                )
                setPending('')
            } else if (kind === 'rollback') {
                setCandidate(
                    await api.post(
                        `/v1/evaluation/candidates/${encodeURIComponent(candidateRef)}/rollback`,
                        undefined,
                        objectSchema,
                    ),
                )
                setPending('')
            }
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }
    return (
        <>
            <div className="page-title">
                <div>
                    <span className="eyebrow">DIAGNOSTICS & EVALUATION</span>
                    <h1>调试与评估</h1>
                    <p>读取结构化事件与计划版本。发布策略需要明确审核。</p>
                </div>
            </div>
            <Panel title="任务诊断" tag="TRACE">
                <form
                    className="search-row"
                    onSubmit={(e) => {
                        e.preventDefault()
                        setReference(input.trim())
                    }}
                >
                    <label className="sr-only" htmlFor="trace-ref">
                        任务编号
                    </label>
                    <input
                        id="trace-ref"
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        placeholder="输入同一主体可访问的任务编号"
                    />
                    <button className="primary">
                        <Search size={17} />
                        读取任务
                    </button>
                </form>
                <ErrorNotice error={task.error ?? trace.error ?? error} />
                <Updated at={trace.dataUpdatedAt} stale={!!trace.error} />
                {task.data && (
                    <>
                        <div className="section-heading">
                            <h3>事件时间线</h3>
                            <button
                                className="secondary"
                                disabled={busy || all}
                                onClick={() => void loadAll()}
                            >
                                {all ? '轨迹已读取完整' : '加载剩余轨迹'}
                            </button>
                        </div>
                        <div className="trace-events">
                            {events.map((event) => (
                                <div key={event.sequence}>
                                    <span>{event.sequence}</span>
                                    <div>
                                        <strong>{event.kind}</strong>
                                        <small>{time(event.occurred_at)}</small>
                                        <Raw value={event.data} title="事件详情" />
                                    </div>
                                </div>
                            ))}
                        </div>
                        {!events.length && <Empty title="尚无已加载事件" />}
                        <p className="muted">
                            已加载 {events.length} 条，按事件序号去重；
                            {all ? '已读取至末页。' : '当前不代表全部轨迹。'}
                        </p>
                        <details className="raw">
                            <summary>计划版本与差异</summary>
                            {task.data.plans.map((p, i) => (
                                <section key={p.version}>
                                    <h3>
                                        版本 {p.version} · {p.planner_version}
                                    </h3>
                                    {i > 0 && (
                                        <p>
                                            {
                                                p.steps.filter(
                                                    (s) =>
                                                        json(s) !==
                                                        json(
                                                            task.data!.plans[i - 1].steps.find(
                                                                (o) => o.step_ref === s.step_ref,
                                                            ),
                                                        ),
                                                ).length
                                            }{' '}
                                            个当前节点与上一版本不同；删除节点请对照完整计划。
                                        </p>
                                    )}
                                    <Raw value={p.steps} title="完整计划与依赖" />
                                </section>
                            ))}
                        </details>
                        <Notice>
                            模型轨迹仅展示提供方、轮次、耗时、用量和工具状态。未提供的用量不是零，不展示隐藏推理内容。
                        </Notice>
                    </>
                )}
            </Panel>
            {canEvaluate && (
                <Panel title="离线回放与候选策略" tag="OPERATOR ONLY">
                    <Notice>
                        需要服务端启用评估功能。普通运行环境可能返回“尚未启用”。候选列表与全量发布历史尚无接口，此处仅按编号读取。对照评估可能使用服务端配置的模型并产生费用，须明确点击发起。
                    </Notice>
                    <div className="form-grid">
                        <div>
                            <Field label="新候选提示词">
                                <textarea
                                    rows={4}
                                    disabled={busy}
                                    value={prompt}
                                    onChange={(e) => setPrompt(e.target.value)}
                                    placeholder="输入待隔离评估的提示词"
                                />
                            </Field>
                            <button
                                className="secondary"
                                disabled={busy || !prompt.trim()}
                                onClick={() => void evaluation('create')}
                            >
                                创建候选草案
                            </button>
                        </div>
                        <div>
                            <Field label="候选编号">
                                <input
                                    disabled={busy}
                                    value={candidateRef}
                                    onChange={(e) => {
                                        setCandidateRef(e.target.value)
                                        setCandidate(null)
                                        setReport(null)
                                    }}
                                />
                            </Field>
                            <button
                                className="secondary"
                                disabled={busy || !candidateRef}
                                onClick={() => void evaluation('load')}
                            >
                                按编号读取
                            </button>
                        </div>
                    </div>
                    <Field label="回放套件 JSON（最多 5 个用例）">
                        <textarea
                            className="code-input"
                            rows={6}
                            disabled={busy}
                            value={suite}
                            onChange={(e) => setSuite(e.target.value)}
                            placeholder="粘贴经检查的隔离回放套件"
                        />
                    </Field>
                    <div className="actions">
                        <button
                            className="secondary"
                            disabled={busy || !suite}
                            onClick={() => void evaluation('replay')}
                        >
                            运行固定目标回放
                        </button>
                        <button
                            className="secondary"
                            disabled={busy || !suite || !candidateRef}
                            onClick={() => setPending('evaluate')}
                        >
                            发起候选对照评估
                        </button>
                    </div>
                    <ErrorNotice error={error} />
                    {candidate && <Raw value={candidate} title="当前候选详情" />}
                    {report && (
                        <>
                            <Raw value={report} title="完整评估结果与基线" />
                            <label className="check">
                                <input
                                    type="checkbox"
                                    checked={reviewed}
                                    onChange={(e) => setReviewed(e.target.checked)}
                                />
                                我已审核本次评估摘要与基线
                            </label>
                        </>
                    )}
                    <div className="actions">
                        <button
                            className="primary"
                            disabled={busy || !reviewed || report?.eligible_for_release !== true}
                            onClick={() => setPending('release')}
                        >
                            审核后发布策略
                        </button>
                        <button
                            className="secondary"
                            disabled={busy || !candidateRef || candidate?.status !== 'RELEASED'}
                            onClick={() => setPending('rollback')}
                        >
                            回退当前策略
                        </button>
                    </div>
                </Panel>
            )}
            {pending && (
                <Confirm
                    title={
                        pending === 'release'
                            ? '发布已审核策略？'
                            : pending === 'rollback'
                              ? '回退当前策略？'
                              : '发起候选对照评估？'
                    }
                    description={
                        pending === 'evaluate'
                            ? '将调用服务端配置的模型，可能产生模型费用。业务使用隔离的虚构数据。'
                            : pending === 'release'
                              ? '只发布当前评估摘要绑定的策略；服务端会重新校验。业务授权不会因此改变。'
                              : '回退仅影响规划策略，已完成业务和授权记录保持不变。'
                    }
                    onClose={() => setPending('')}
                    onConfirm={() => void evaluation(pending)}
                    busy={busy}
                />
            )}
        </>
    )
}
