import { AlertCircle, CheckCircle2, CircleHelp, LoaderCircle, ShieldCheck, X } from 'lucide-react'
import { useLayoutEffect, useRef, type ReactNode } from 'react'
import { ApiError, time } from './api'
import { money } from './money'
import { tone } from './state'

export function Money({ value, large = false }: { value: unknown; large?: boolean }) {
    return (
        <span className={large ? 'money large' : 'money'}>
            {money(value)} <small>CNY</small>
        </span>
    )
}
export function Badge({ children, status = 'neutral' }: { children: ReactNode; status?: string }) {
    return (
        <span
            className={`badge ${['success', 'danger', 'warning', 'neutral'].includes(status) ? status : tone(status)}`}
        >
            <span className="status-dot" />
            {children}
        </span>
    )
}
export function ErrorNotice({ error }: { error: unknown }) {
    if (!error) return null
    return (
        <div role="alert" className="notice danger">
            <AlertCircle size={18} />
            <div>
                <strong>
                    {error instanceof Error ? error.message : '操作未成功，请核对服务状态。'}
                </strong>
                {error instanceof ApiError && (
                    <>
                        <small>{error.code}</small>
                        {error.details.length > 0 && (
                            <p>请检查表单字段与必填项，原有输入已保留。</p>
                        )}
                    </>
                )}
            </div>
        </div>
    )
}
export function Notice({ children, kind = 'neutral' }: { children: ReactNode; kind?: string }) {
    return (
        <div className={`notice ${kind}`}>
            <CircleHelp size={18} />
            <div>{children}</div>
        </div>
    )
}
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
    return (
        <div className="empty">
            <div className="empty-icon">
                <ShieldCheck size={26} />
            </div>
            <h3>{title}</h3>
            {children && <p>{children}</p>}
        </div>
    )
}
export function Loading() {
    return (
        <div role="status" className="loading">
            <LoaderCircle className="spin" size={19} /> 正在读取服务端状态…
        </div>
    )
}
export function Panel({
    title,
    tag,
    children,
    actions,
}: {
    title: string
    tag?: string
    children: ReactNode
    actions?: ReactNode
}) {
    return (
        <section className="panel">
            <div className="panel-heading">
                <div>
                    <span className="eyebrow">{tag}</span>
                    <h2>{title}</h2>
                </div>
                {actions}
            </div>
            {children}
        </section>
    )
}
export function Field({
    label,
    children,
    hint,
}: {
    label: string
    children: ReactNode
    hint?: string
}) {
    return (
        <label className="field">
            <span>{label}</span>
            {children}
            {hint && <small>{hint}</small>}
        </label>
    )
}
export function Raw({ value, title = '查看原始数据' }: { value: unknown; title?: string }) {
    return (
        <details className="raw">
            <summary>{title}</summary>
            <pre>{safeJson(value)}</pre>
        </details>
    )
}
function safeJson(value: unknown) {
    return JSON.stringify(value, (_, v) => (typeof v === 'bigint' ? v.toString() : v), 2)
}
export function Updated({ at, stale = false }: { at: number; stale?: boolean }) {
    return at > 0 ? (
        <span className={`updated ${stale ? 'stale' : ''}`}>
            {stale ? '更新中断 · ' : ''}最后读取 {time(new Date(at).toISOString())}
        </span>
    ) : null
}
export function Modal({
    title,
    onClose,
    children,
}: {
    title: string
    onClose: () => void
    children: ReactNode
}) {
    const ref = useRef<HTMLDialogElement | null>(null)
    useLayoutEffect(() => {
        const dialog = ref.current!
        const trigger = document.activeElement
        dialog.showModal()
        return () => {
            dialog.close()
            if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus()
        }
    }, [])
    return (
        <dialog
            ref={ref}
            className="modal"
            onCancel={onClose}
            onClick={(e) => {
                if (e.target === e.currentTarget) onClose()
            }}
            aria-label={title}
        >
            <div className="modal-head">
                <h2>{title}</h2>
                <button className="icon-button" onClick={onClose} aria-label="关闭">
                    <X size={20} />
                </button>
            </div>
            {children}
        </dialog>
    )
}
export function Confirm({
    title,
    description,
    onConfirm,
    onClose,
    busy = false,
}: {
    title: string
    description: string
    onConfirm: () => void
    onClose: () => void
    busy?: boolean
}) {
    return (
        <Modal title={title} onClose={onClose}>
            <p>{description}</p>
            <div className="actions">
                <button className="secondary" onClick={onClose}>
                    返回
                </button>
                <button className="primary" disabled={busy} onClick={onConfirm}>
                    确认{busy ? '处理中…' : ''}
                </button>
            </div>
        </Modal>
    )
}
export function VerifiedMark() {
    return (
        <div className="verified-mark">
            <CheckCircle2 size={20} /> 业务证据已核实
        </div>
    )
}
