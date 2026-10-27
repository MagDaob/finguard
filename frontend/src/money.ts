import { isLosslessNumber, parse, stringify } from 'lossless-json'

export type Minor = number | bigint

export function parseWire(source: string): unknown {
    return parse(source, (_key, value) => {
        if (!isLosslessNumber(value)) return value
        if (/^-?\d+$/.test(value.value)) {
            const n = BigInt(value.value)
            return n <= BigInt(Number.MAX_SAFE_INTEGER) && n >= BigInt(Number.MIN_SAFE_INTEGER)
                ? Number(n)
                : n
        }
        const n = Number(value.value)
        if (!Number.isFinite(n)) throw new Error('响应数字超出可表示范围。')
        return n
    })
}

export const json = (value: unknown, pretty = false) =>
    stringify(value, undefined, pretty ? 2 : undefined) ?? ''
export function canonical(value: unknown): string {
    const sorted = (v: unknown): unknown =>
        Array.isArray(v)
            ? v.map(sorted)
            : v && typeof v === 'object'
              ? Object.fromEntries(
                    Object.entries(v)
                        .sort(([a], [b]) => a.localeCompare(b))
                        .map(([k, x]) => [k, sorted(x)]),
                )
              : v
    return json(sorted(value))
}
export const isMinor = (n: unknown): n is Minor =>
    typeof n === 'bigint' || (typeof n === 'number' && Number.isSafeInteger(n))
export function yuan(source: string): number {
    const value = source.trim()
    if (!/^(0|[1-9]\d*)(\.\d{1,2})?$/.test(value)) throw new Error('金额须为非负数，最多两位小数。')
    const [whole, fraction = ''] = value.split('.')
    const n = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'))
    if (n > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('金额超出本界面支持的安全整数范围。')
    return Number(n)
}
export function decimal(n: Minor): string {
    const b = BigInt(n),
        abs = b < 0n ? -b : b
    return `${b < 0n ? '-' : ''}${abs / 100n}.${String(abs % 100n).padStart(2, '0')}`
}
export function money(n: unknown): string {
    if (!isMinor(n)) return '金额未提供'
    const [whole, cents] = decimal(n).split('.')
    return `¥${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${cents}`
}
export function remaining(balance: unknown, amount: unknown, fee: unknown): Minor | undefined {
    return isMinor(balance) && isMinor(amount) && isMinor(fee)
        ? BigInt(balance) - BigInt(amount) - BigInt(fee)
        : undefined
}
