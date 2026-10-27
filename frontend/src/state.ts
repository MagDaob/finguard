import type { Operation, Task } from './api'

export const taskLabels: Record<string, string> = {
    UNDERSTANDING: '正在理解需求',
    PLANNING: '正在生成计划',
    WAITING_CLARIFICATION: '需要补充信息',
    WAITING_APPROVAL: '等待你的确认',
    EXECUTING: '正在执行',
    PARTIALLY_COMPLETED: '部分完成',
    COMPLETED: '完成状态待核验',
    TERMINATED: '任务已终止',
}
export const operationLabels: Record<string, string> = {
    PREPARING: '准备执行',
    WAITING_APPROVAL: '等待确认',
    AUTHORIZED: '已确认，等待执行',
    SUBMITTING: '正在提交',
    UNKNOWN: '结果待核实',
    SUCCEEDED: '已完成',
    FAILED: '此步骤失败',
    CANCELLED_PRE_SUBMIT: '提交前已取消',
}
export const actionLabels: Record<string, string> = {
    'accounts.balance': '查询余额',
    'transfer.create': '转账',
    'bills.analyze': '分析账单',
    'product.subscribe': '申购产品',
    'product.redeem': '赎回产品',
    'card.set_lock': '临时卡片管理',
    'subscription.cancel': '取消订阅合同',
}
export const actionName = (name: string) => actionLabels[name] ?? '暂不支持的操作'
export const isWrite = (action: string) =>
    [
        'transfer.create',
        'product.subscribe',
        'product.redeem',
        'card.set_lock',
        'subscription.cancel',
    ].includes(action)
export const activeOperations = (task: Task) => {
    const ids = new Set(task.plans.at(-1)?.steps.map((s) => s.operation_ref) ?? [])
    return task.operations.filter((op) => ids.has(op.operation_ref))
}
export const verified = (task: Task) =>
    task.status === 'COMPLETED' &&
    task.verified_success &&
    activeOperations(task).length > 0 &&
    activeOperations(task).every(
        (op) =>
            op.status === 'SUCCEEDED' &&
            task.evidence.some((e) => e.operation_ref === op.operation_ref),
    )
export function taskLabel(task: Task) {
    if (task.status === 'PLANNING' && task.current_version > 0) return '计划已生成'
    return verified(task) ? '已完成并核实' : (taskLabels[task.status] ?? '暂无法识别的状态')
}
export function operationLabel(op: Operation) {
    if (
        op.status === 'UNKNOWN' &&
        op.action === 'product.redeem' &&
        op.result?.status === 'PENDING' &&
        op.result?.funds_available === false
    )
        return '已受理，等待到账'
    return operationLabels[op.status] ?? '暂无法识别的状态'
}
export function tone(status: string) {
    return status === 'SUCCEEDED'
        ? 'success'
        : ['FAILED', 'TERMINATED', 'CANCELLED_PRE_SUBMIT'].includes(status)
          ? 'danger'
          : ['WAITING_APPROVAL', 'WAITING_CLARIFICATION', 'AUTHORIZED'].includes(status)
            ? 'warning'
            : 'neutral'
}
export const editable = (op: Operation) =>
    ['PREPARING', 'WAITING_APPROVAL', 'AUTHORIZED'].includes(op.status)
export const pollInterval = (status: string, failures: number, visible: boolean) =>
    !visible
        ? false
        : failures
          ? Math.min(30000, 2000 * 2 ** (failures - 1))
          : ['COMPLETED', 'TERMINATED'].includes(status)
            ? false
            : ['WAITING_APPROVAL', 'WAITING_CLARIFICATION'].includes(status)
              ? 10000
              : 2000
export function stopMessage() {
    return '未提交步骤将取消；已提交操作继续核实，已完成结果保留。'
}
