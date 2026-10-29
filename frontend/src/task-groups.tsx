import { useQuery, useInfiniteQuery } from '@tanstack/react-query'
import { Link, NavLink, useParams } from 'react-router-dom'
import { useState } from 'react'
import { z } from 'zod'
import { FolderKanban, ChevronRight } from 'lucide-react'
import { useSession } from './session'
import { useVisible } from './hooks'
import { taskRowSchema, time } from './api'
import { Badge, Empty, ErrorNotice, Loading } from './ui'
import { taskLabels } from './state'
const groups = [
    ['pending', '待完成'],
    ['completed', '已完成'],
    ['ended', '已结束'],
    ['all', '全部任务'],
] as const
const countsSchema = z.object({
    pending: z.number(),
    completed: z.number(),
    ended: z.number(),
    all: z.number(),
})
export function TaskMenu() {
    const { api, me } = useSession(),
        visible = useVisible(),
        [open, setOpen] = useState(false)
    const counts = useQuery({
        queryKey: [me?.subject_ref, 'task-group-counts'],
        enabled: !!api && !!me?.scopes.includes('tasks.read'),
        queryFn: ({ signal }) => api!.get('/v1/task-groups/counts', countsSchema, signal),
        refetchInterval: visible ? 10000 : false,
    })
    return (
        <div className="task-menu">
            <button
                aria-expanded={open}
                aria-controls="task-group-menu"
                onClick={() => setOpen(!open)}
            >
                <FolderKanban size={19} />
                <span>任务</span>
                <ChevronRight size={16} className={open ? 'expanded' : ''} />
            </button>
            {open && (
                <div id="task-group-menu">
                    {groups.map(([value, label]) => (
                        <NavLink
                            key={value}
                            to={'/tasks/list/' + value}
                            aria-label={`${label} ${counts.data?.[value] ?? '—'}`}
                        >
                            <span>{label}</span>
                            <small>{counts.data?.[value] ?? '—'}</small>
                        </NavLink>
                    ))}
                    <ErrorNotice error={counts.error} />
                </div>
            )}
        </div>
    )
}
export function TaskGroupPage() {
    const { group = 'all' } = useParams(),
        { api, me } = useSession(),
        visible = useVisible()
    const rows = useInfiniteQuery({
        queryKey: [me?.subject_ref, 'task-groups', group],
        enabled: !!api,
        initialPageParam: '',
        queryFn: ({ pageParam, signal }) =>
            api!.get(
                '/v1/task-groups/' +
                    encodeURIComponent(group) +
                    (pageParam ? '?before=' + encodeURIComponent(pageParam) : ''),
                z.array(taskRowSchema.extend({ task_group: z.string() })),
                signal,
            ),
        getNextPageParam: (last) => (last.length === 25 ? last.at(-1)?.task_ref : undefined),
        refetchInterval: visible ? 10000 : false,
    })
    return (
        <section>
            <div className="page-title">
                <div>
                    <span className="eyebrow">TASKS</span>
                    <h1>{groups.find((g) => g[0] === group)?.[1] ?? '任务'}</h1>
                    <p>按服务端状态分类；已完成同时要求成功证据。</p>
                </div>
                <Link to="/tasks" className="secondary">
                    新对话
                </Link>
            </div>
            <ErrorNotice error={rows.error} />
            {rows.isLoading && <Loading />}
            {rows.data?.pages.flat().map((task) => (
                <Link className="group-task" key={task.task_ref} to={'/tasks/' + task.task_ref}>
                    <div>
                        <strong>{task.user_request}</strong>
                        <Badge status={task.task_group === 'completed' ? 'success' : task.status}>
                            {task.task_group === 'completed'
                                ? '已完成并核实'
                                : task.status === 'COMPLETED'
                                  ? '完成状态待核实'
                                  : (taskLabels[task.status] ?? task.status)}
                        </Badge>
                    </div>
                    <small>
                        {time(task.created_at)} · 计划版本 {task.current_version}
                    </small>
                </Link>
            ))}
            {rows.data && !rows.data.pages.flat().length && (
                <Empty title="此分类暂无任务">聊天和待澄清目标不会出现在任务列表中。</Empty>
            )}
            {rows.hasNextPage && (
                <button
                    className="secondary"
                    disabled={rows.isFetchingNextPage}
                    onClick={() => void rows.fetchNextPage()}
                >
                    加载更多任务
                </button>
            )}
        </section>
    )
}
