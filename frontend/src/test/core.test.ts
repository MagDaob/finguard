import { describe, expect, it, vi, afterEach } from 'vitest'
import { z } from 'zod'
import { Api, ApiError, type Task } from '../api'
import { json, money, parseWire, yuan } from '../money'
import { monthWindow } from '../business'
import { pollInterval, taskLabel, verified } from '../state'

afterEach(() => vi.unstubAllGlobals())
describe('integer money boundary', () => {
    it.each([
        ['0', 0],
        ['300.01', 30001],
        ['0.01', 1],
        ['90071992547409.91', Number.MAX_SAFE_INTEGER],
    ])('converts %s by integer arithmetic', (value, minor) => expect(yuan(value)).toBe(minor))
    it.each(['-1', '1.001', '1e3', '90071992547409.92', 'NaN', '0x10', '01.00'])(
        'rejects unsafe %s',
        (v) => expect(() => yuan(v)).toThrow(),
    )
    it('parses and roundtrips amounts beyond JavaScript safe integers without rounding', () => {
        const data = parseWire('{"amount_minor":9007199254740993}') as { amount_minor: bigint }
        expect(data.amount_minor).toBe(9007199254740993n)
        expect(money(data.amount_minor)).toBe('¥90,071,992,547,409.93')
        expect(json(data)).toBe('{"amount_minor":9007199254740993}')
    })
    it('keeps unknown values distinct from zero', () => {
        expect(money(undefined)).toBe('金额未提供')
        expect(money(0)).toContain('0.00')
    })
    it('uses a half-open calendar month across leap years', () => {
        expect(monthWindow('2024-02')).toEqual({
            start_date: '2024-02-01',
            end_date: '2024-03-01',
            timezone: 'Asia/Shanghai',
        })
        expect(monthWindow('2026-12').end_date).toBe('2027-01-01')
    })
})
describe('API safety', () => {
    it('clears session for 401 even when the body is not JSON', async () => {
        const expired = vi.fn()
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('not json', { status: 401 })))
        await expect(
            new Api('fixture-token', expired).get('/v1/me', z.unknown()),
        ).rejects.toMatchObject({ status: 401 })
        expect(expired).toHaveBeenCalledOnce()
    })
    it('never automatically retries a write', async () => {
        const fetcher = vi.fn().mockRejectedValue(new Error('offline'))
        vi.stubGlobal('fetch', fetcher)
        await expect(
            new Api('fixture-token', vi.fn()).post('/v1/tasks', {}, z.unknown()),
        ).rejects.toBeInstanceOf(ApiError)
        expect(fetcher).toHaveBeenCalledOnce()
    })
    it('drops stale responses after identity changes', async () => {
        let active = true
        let resolve!: (r: Response) => void
        vi.stubGlobal(
            'fetch',
            vi.fn(() => new Promise<Response>((r) => (resolve = r))),
        )
        const expired = vi.fn(),
            api = new Api('fixture-token', expired, () => active)
        const request = api.get('/v1/me', z.unknown())
        active = false
        resolve(new Response('{}'))
        await expect(request).rejects.toMatchObject({ code: 'SESSION_CHANGED' })
        expect(expired).not.toHaveBeenCalled()
    })
    it('rejects malformed success payloads', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"verified_success":true}')))
        await expect(
            new Api('fixture-token', vi.fn()).get('/v1/me', z.object({ subject_ref: z.string() })),
        ).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
    })
})
describe('conservative status display', () => {
    const op = {
        operation_ref: 'op-1',
        step_ref: 's',
        action: 'transfer.create',
        arguments: {},
        digest: 'digest',
        status: 'SUCCEEDED',
    }
    const task = {
        task_ref: 'task-1',
        status: 'COMPLETED',
        verified_success: true,
        operations: [op],
        plans: [
            {
                version: 1,
                planner_version: 'fixture',
                steps: [{ ...op, depends_on: [], funds_available_after: [] }],
            },
        ],
        evidence: [{ operation_ref: 'op-1' }],
    } as unknown as Task
    it('requires status, verified flag, operation success and associated evidence', () => {
        expect(verified(task)).toBe(true)
        expect(verified({ ...task, evidence: [] })).toBe(false)
        expect(verified({ ...task, verified_success: false })).toBe(false)
        expect(taskLabel({ ...task, status: 'UNRECOGNIZED' })).toBe('暂无法识别的状态')
    })
    it('ignores obsolete operations outside the current plan', () =>
        expect(
            verified({
                ...task,
                operations: [
                    ...task.operations,
                    { ...op, operation_ref: 'old-op', status: 'FAILED' },
                ],
            }),
        ).toBe(true))
    it('pauses polling when hidden and slows while awaiting confirmation', () => {
        expect(pollInterval('EXECUTING', 0, false)).toBe(false)
        expect(pollInterval('WAITING_APPROVAL', 0, true)).toBe(10000)
        expect(pollInterval('EXECUTING', 5, true)).toBe(30000)
        expect(pollInterval('COMPLETED', 0, true)).toBe(false)
    })
})
