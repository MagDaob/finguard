import { z } from 'zod'
import { isMinor, json, parseWire, type Minor } from './money'

const record = z.record(z.string(), z.unknown())
const argumentsRecord = record.superRefine((value, ctx) => {
    for (const [key, item] of Object.entries(value)) {
        if (key.endsWith('_minor') && (!isMinor(item) || BigInt(item) < 0n))
            ctx.addIssue({ code: 'custom', path: [key], message: '操作金额必须为非负整数分' })
    }
})
const minor = z.custom<Minor>(isMinor, '金额不是有效整数分')
export const stepSchema = z.object({
    step_ref: z.string(),
    action: z.string(),
    arguments: argumentsRecord,
    depends_on: z.array(z.string()).default([]),
    funds_available_after: z.array(z.string()).default([]),
    operation_ref: z.string().optional(),
})
export type Step = z.infer<typeof stepSchema>
export const operationSchema = z.object({
    operation_ref: z.string(),
    step_ref: z.string(),
    action: z.string(),
    status: z.string(),
    arguments: argumentsRecord,
    digest: z.string(),
    error_code: z.string().nullable().optional(),
    result: record.nullable().optional(),
    delegation_ref: z.string().nullable().optional(),
})
export type Operation = z.infer<typeof operationSchema>
export const taskSchema = z.object({
    task_ref: z.string(),
    subject_ref: z.string(),
    user_request: z.string(),
    status: z.string(),
    current_version: z.number().int().nonnegative(),
    verified_success: z.boolean(),
    stop_requested: z.boolean(),
    created_at: z.string(),
    updated_at: z.string(),
    context_task_ref: z.string().nullable().optional(),
    constraints: record,
    questions: z.array(
        z.object({
            field: z.string(),
            message: z.string(),
            options: z.array(z.string()).default([]),
        }),
    ),
    operations: z.array(operationSchema),
    plans: z.array(
        z.object({
            version: z.number().int(),
            steps: z.array(stepSchema),
            planner_version: z.string(),
        }),
    ),
    evidence: z.array(
        z.object({
            evidence_ref: z.string(),
            operation_ref: z.string(),
            kind: z.string(),
            record_ref: z.string(),
            source: z.string(),
            data: record,
            verified_at: z.string(),
        }),
    ),
})
export type Task = z.infer<typeof taskSchema>
export const taskRowSchema = z.object({
    task_ref: z.string(),
    user_request: z.string(),
    status: z.string(),
    current_version: z.number().int(),
    stop_requested: z.boolean(),
    created_at: z.string(),
})
export type TaskRow = z.infer<typeof taskRowSchema>
export const accountSchema = z.object({
    account_ref: z.string(),
    balance_minor: minor,
    currency: z.literal('CNY'),
    active: z.boolean(),
})
export type Account = z.infer<typeof accountSchema>
export const recipientSchema = z.object({
    recipient_ref: z.string(),
    display_name: z.string(),
    active: z.boolean(),
})
export const meSchema = z.object({ subject_ref: z.string(), scopes: z.array(z.string()) })
export type Me = z.infer<typeof meSchema>
export const previewSchema = z.object({
    operation_ref: z.string(),
    digest: z.string(),
    authentication_policy: z.object({
        level: z.string(),
        step_up_required: z.boolean(),
        step_up_available: z.boolean(),
        threshold_minor: minor,
    }),
    preview: record,
})
export type Preview = z.infer<typeof previewSchema>
export const eventSchema = z.object({
    sequence: z.number().int().safe(),
    kind: z.string(),
    data: record,
    occurred_at: z.string(),
})
export type TraceEvent = z.infer<typeof eventSchema>
export const resourceSchema = z.object({
    resource_ref: z.string(),
    state: z.string(),
    data: record,
})
export const productSchema = z
    .object({
        product_ref: z.string(),
        name: z.string(),
        risk_level: z.number(),
        trading_open: z.boolean(),
        risk_eligible: z.boolean(),
        simulation_fixed_value: z.boolean(),
    })
    .passthrough()
export const delegationSchema = z
    .object({
        delegation_ref: z.string(),
        specification: record,
        used_minor: minor,
        revoked: z.boolean(),
        expires_at: z.string(),
    })
    .passthrough()
export const approvalSchema = z
    .object({ approval_ref: z.string(), status: z.string(), expires_at: z.string() })
    .passthrough()
export type Approval = z.infer<typeof approvalSchema>
export const ledgerSchema = z
    .object({
        entry_ref: z.string(),
        amount_minor: minor,
        category: z.string(),
        occurred_at: z.string(),
    })
    .passthrough()
export interface TaskInput {
    client_request_ref: string
    user_request: string
    steps?: Step[]
    context_task_ref?: string
    constraints?: { minimum_remaining_balance_minor: number; maximum_fee_minor: number }
}

export class ApiError extends Error {
    constructor(
        public code: string,
        message: string,
        public status = 0,
        public details: unknown[] = [],
    ) {
        super(message)
    }
}
export class Api {
    constructor(
        private token: string,
        private expired: () => void,
        private active: () => boolean = () => true,
    ) {}
    async request<T>(
        method: string,
        path: string,
        body: unknown,
        schema: z.ZodType<T>,
        signal?: AbortSignal,
    ): Promise<T> {
        if (!this.active())
            throw new ApiError('SESSION_CHANGED', '访问会话已变化，请读取当前状态。', 409)
        let response: Response
        try {
            response = await fetch(path, {
                method,
                headers: {
                    Authorization: `Bearer ${this.token}`,
                    ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
                },
                body: body === undefined ? undefined : json(body),
                signal,
                cache: 'no-store',
                redirect: 'error',
            })
        } catch (e) {
            if (signal?.aborted) throw e
            throw new ApiError('NETWORK_UNAVAILABLE', '网络连接中断，请先核对原任务状态。')
        }
        if (!this.active())
            throw new ApiError('SESSION_CHANGED', '访问会话已变化，已丢弃旧响应。', 409)
        if (response.status === 401) {
            this.expired()
            throw new ApiError('UNAUTHENTICATED', '会话已失效，请重新建立访问会话。', 401)
        }
        let data: unknown
        try {
            data = parseWire(await response.text())
        } catch {
            throw new ApiError(
                'INVALID_RESPONSE',
                '服务响应无法识别，未将其视为成功。',
                response.status,
            )
        }
        if (!response.ok) {
            const error = z
                .object({
                    error: z.object({
                        code: z.string(),
                        message: z.string().optional(),
                        details: z.array(z.unknown()).optional(),
                    }),
                })
                .safeParse(data)
            throw new ApiError(
                error.success ? error.data.error.code : 'REQUEST_FAILED',
                error.success
                    ? (error.data.error.message ?? '请求参数不符合要求。')
                    : '请求未成功，请重新读取状态。',
                response.status,
                error.success ? error.data.error.details : [],
            )
        }
        const checked = schema.safeParse(data)
        if (!checked.success)
            throw new ApiError('INVALID_RESPONSE', '响应结构与接口契约不符，已停止此次操作。', 502)
        return checked.data
    }
    get<T>(path: string, schema: z.ZodType<T>, signal?: AbortSignal) {
        return this.request('GET', path, undefined, schema, signal)
    }
    post<T>(path: string, body: unknown, schema: z.ZodType<T>) {
        return this.request('POST', path, body, schema)
    }
    task(ref: string, signal?: AbortSignal) {
        return this.get(`/v1/tasks/${encodeURIComponent(ref)}`, taskSchema, signal)
    }
    preview(ref: string) {
        return this.get(`/v1/operations/${encodeURIComponent(ref)}/preview`, previewSchema)
    }
}
export const objectSchema = record
export const objectsSchema = z.array(record)
export const newRef = () => `web-${crypto.randomUUID()}`
export const text = (value: unknown, fallback = '未提供') =>
    typeof value === 'string' ? value : fallback
export const time = (value: unknown) =>
    typeof value === 'string' && !Number.isNaN(Date.parse(value))
        ? new Date(value).toLocaleString('zh-CN', { hour12: false })
        : '时间未提供'
