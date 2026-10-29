"""任务分类在服务端计算；完成分类同时要求当前计划与业务证据。"""

from finguard.backend.errors import require
from finguard.backend.privacy import redact_text

CTE = """WITH classified AS (
 SELECT t.*,
 CASE WHEN t.status='COMPLETED' AND EXISTS (
 SELECT 1 FROM plans p WHERE p.task_ref=t.task_ref AND p.version=t.current_version AND jsonb_array_length(p.steps)>0
 AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p.steps) s LEFT JOIN operations o ON o.operation_ref=s->>'operation_ref'
 WHERE o.task_ref IS DISTINCT FROM t.task_ref OR o.status IS DISTINCT FROM 'SUCCEEDED' OR NOT EXISTS (
 SELECT 1 FROM evidence e WHERE e.operation_ref=o.operation_ref AND e.subject_ref=t.subject_ref))) THEN 'completed'
 WHEN t.status='TERMINATED' THEN 'ended' ELSE 'pending' END AS task_group
 FROM tasks t WHERE t.subject_ref=%s
) """


def counts(backend, actor):
    backend.security.require_scope(actor, "tasks.read")
    with backend.db.transaction() as c:
        rows = c.execute(
            CTE + "SELECT task_group,count(*) AS total FROM classified GROUP BY task_group",
            (actor.subject_ref,),
        ).fetchall()
    result = {"pending": 0, "completed": 0, "ended": 0}
    result.update({r["task_group"]: r["total"] for r in rows})
    return {**result, "all": sum(result.values())}


def listing(backend, actor, group, before=None, limit=25):
    backend.security.require_scope(actor, "tasks.read")
    require(
        group in {"all", "pending", "completed", "ended"},
        "INVALID_TASK_GROUP",
        "任务分类无效。",
        422,
    )
    with backend.db.transaction() as c:
        rows = c.execute(
            CTE
            + "SELECT task_ref,user_request,status,current_version,stop_requested,created_at,task_group FROM classified WHERE (%s='all' OR task_group=%s) AND (%s::text IS NULL OR task_ref<%s) ORDER BY task_ref DESC LIMIT %s",
            (actor.subject_ref, group, group, before, before, limit),
        ).fetchall()
    return [{**r, "user_request": redact_text(r["user_request"])} for r in rows]
