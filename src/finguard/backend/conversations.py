"""持久化对话与任务关联；对话不能产生业务授权。"""

import re

from psycopg.types.json import Jsonb
from pydantic import Field

from finguard.backend.catalog import ReplanRequest, RequestModel, TaskCreate, digest
from finguard.backend.errors import ServiceError, require
from finguard.backend.privacy import redact_text
from finguard.backend.understanding import money_minor
from finguard.domain.values import Reference, RequestText


class MessageRequest(RequestModel):
    message_ref: Reference
    content: RequestText
    expected_sequence: int = Field(strict=True, ge=0)


def plan_steps(task):
    return (
        [
            {
                k: s[k]
                for k in ("step_ref", "action", "arguments", "depends_on", "funds_available_after")
            }
            for s in task["plans"][-1]["steps"]
        ]
        if task["plans"]
        else []
    )


def same_steps(left, right):
    return digest(sorted(left, key=lambda s: s["step_ref"])) == digest(
        sorted(right, key=lambda s: s["step_ref"])
    )


class Conversations:
    def __init__(self, backend):
        self.backend = backend
        self.db = backend.db
        self.security = backend.security

    def _owner(self, c, actor, reference):
        row = c.execute(
            "SELECT * FROM conversations WHERE conversation_ref=%s AND subject_ref=%s",
            (reference, actor.subject_ref),
        ).fetchone()
        require(row is not None, "NOT_FOUND", "对话不存在。", 404)
        return row

    def list(self, actor, before=None, limit=25):
        self.security.require_scope(actor, "tasks.read")
        with self.db.transaction() as c:
            return c.execute(
                "SELECT c.*, COALESCE((SELECT user_text FROM conversation_turns t WHERE t.conversation_ref=c.conversation_ref ORDER BY sequence LIMIT 1),'新对话') AS title FROM conversations c WHERE subject_ref=%s AND (%s::text IS NULL OR (c.created_at,c.conversation_ref)<(SELECT created_at,conversation_ref FROM conversations WHERE conversation_ref=%s AND subject_ref=%s)) ORDER BY c.created_at DESC,c.conversation_ref DESC LIMIT %s",
                (actor.subject_ref, before, before, actor.subject_ref, limit),
            ).fetchall()

    def get(self, actor, reference):
        self.security.require_scope(actor, "tasks.read")
        with self.db.transaction() as c:
            row = self._owner(c, actor, reference)
            turns = c.execute(
                "SELECT sequence,message_ref,user_text,task_ref,ready,reply,created_at FROM conversation_turns WHERE conversation_ref=%s ORDER BY sequence",
                (reference,),
            ).fetchall()
        return {**row, "turns": turns}

    def _prepare(self, actor, content, previous):
        if re.fullmatch(r"\s*(好[的]?|确认|继续|执行|可以)[。！!\s]*", content):
            return {"kind": "reply"}, "请使用当前操作卡片确认。对话文字不会签发付款授权。"
        match = re.fullmatch(
            r"\s*(?:改成|改为|金额改成|金额改为)\s*([零〇一二两三四五六七八九十百千万亿\d.]+)\s*元?[。！!\s]*",
            content,
        )
        if match:
            require(previous is not None, "NO_CONTEXT", "请先描述要修改的转账任务。", 409)
            steps = plan_steps(previous)
            transfers = [s for s in steps if s["action"] == "transfer.create"]
            require(
                len(transfers) == 1,
                "AMBIGUOUS_EDIT",
                "请明确要修改的操作；可使用完整计划编辑。",
                409,
            )
            item = next(
                o
                for o in previous["operations"]
                if o["step_ref"] == transfers[0]["step_ref"]
                and o["operation_ref"]
                == next(
                    s["operation_ref"]
                    for s in previous["plans"][-1]["steps"]
                    if s["step_ref"] == transfers[0]["step_ref"]
                )
            )
            require(
                item["status"] in {"PREPARING", "WAITING_APPROVAL", "AUTHORIZED"},
                "IMMUTABLE_OPERATION",
                "这笔转账已提交或结束，不能修改；待核实操作须先核实。",
                409,
            )
            transfers[0]["arguments"]["amount_minor"] = money_minor(match[1])
            return {
                "kind": "replan",
                "task_ref": previous["task_ref"],
                "expected_version": previous["current_version"],
                "steps": steps,
            }, "已更新未提交的转账金额。请重新核对当前预览，原确认不适用于新参数。"
        if previous and previous["status"] == "WAITING_CLARIFICATION":
            amount = re.fullmatch(
                r"(?:金额(?:是|为)?|补充金额[:：]?)?\s*([零〇一二两三四五六七八九十百千万\d.]+)\s*元",
                content.strip(),
            )
            prompt = content.removeprefix("完整目标：").removeprefix("完整目标:")
            if amount:
                prompt, replacements = re.subn(
                    r"(转账|转|打款)(?=\s*(?:[，,；;]|$))",
                    lambda m: m[1] + amount[1] + "元",
                    previous["user_request"],
                )
                require(
                    replacements == 1,
                    "AMBIGUOUS_CLARIFICATION",
                    "请以“完整目标：”开头重述全部目标。",
                    409,
                )
            elif prompt == content:
                return {
                    "kind": "reply"
                }, "请以“完整目标：”开头重述全部目标，或补充单笔转账金额，例如“300元”。"
            steps, _, questions = self.backend.planner.infer(
                prompt, self.backend._context(actor, prompt), self._constraints(previous)
            )
            if questions:
                return {"kind": "reply"}, "仍需补充：" + "；".join(q["message"] for q in questions)
            return {
                "kind": "replan",
                "task_ref": previous["task_ref"],
                "expected_version": previous["current_version"],
                "steps": [
                    s.model_dump(mode="json") if hasattr(s, "model_dump") else s for s in steps
                ],
            }, "已补全目标并更新原任务。资金操作仍需在卡片中确认。"
        return {
            "kind": "create",
            "context_task_ref": previous["task_ref"] if previous else None,
        }, "已识别目标并登记任务，查询会自动推进；资金操作请核对确认卡。"

    @staticmethod
    def _constraints(task):
        from finguard.backend.catalog import Constraints

        return Constraints.model_validate(task["constraints"])

    def send(self, actor, reference, request):
        self.security.require_scope(actor, "tasks.write")
        self.security.require_scope(actor, "tasks.read")
        request_hash = digest(
            {**request.model_dump(mode="json"), "content": redact_text(request.content)}
        )
        with self.db.task_lock("conversation:" + reference) as acquired:
            require(acquired, "CONVERSATION_BUSY", "对话正在处理，请稍后核对原消息。", 409)
            with self.db.transaction() as c:
                existing_chat = c.execute(
                    "SELECT subject_ref FROM conversations WHERE conversation_ref=%s", (reference,)
                ).fetchone()
                if existing_chat:
                    self._owner(c, actor, reference)
                else:
                    require(request.expected_sequence == 0, "NOT_FOUND", "对话不存在。", 404)
                    c.execute(
                        "INSERT INTO conversations(conversation_ref,subject_ref) VALUES (%s,%s)",
                        (reference, actor.subject_ref),
                    )
                turn = c.execute(
                    "SELECT * FROM conversation_turns WHERE conversation_ref=%s AND message_ref=%s",
                    (reference, request.message_ref),
                ).fetchone()
                last = c.execute(
                    "SELECT * FROM conversation_turns WHERE conversation_ref=%s ORDER BY sequence DESC LIMIT 1",
                    (reference,),
                ).fetchone()
            if turn:
                require(
                    turn["request_digest"] == request_hash,
                    "REQUEST_CONFLICT",
                    "消息编号已绑定其他内容。",
                    409,
                )
                if turn["ready"]:
                    return self.get(actor, reference)
            else:
                require(
                    not last or last["ready"],
                    "MESSAGE_PENDING",
                    "上一条消息尚未处理完成，请先恢复原消息。",
                    409,
                )
                require(
                    request.expected_sequence == (last["sequence"] if last else 0),
                    "CONVERSATION_CHANGED",
                    "对话已更新，请读取后重新发送。",
                    409,
                )
                require(
                    not last or last["sequence"] < 100,
                    "CONVERSATION_FULL",
                    "此对话已达到 100 条消息，请新建对话。",
                    409,
                )
                with self.db.transaction() as c:
                    prior = c.execute(
                        "SELECT task_ref FROM conversation_turns WHERE conversation_ref=%s AND task_ref IS NOT NULL ORDER BY sequence DESC LIMIT 1",
                        (reference,),
                    ).fetchone()
                previous = self.backend.get_task(actor, prior["task_ref"]) if prior else None
                payload, reply = self._prepare(actor, request.content, previous)
                with self.db.transaction() as c:
                    self.security.require_scope(actor, "tasks.write", c)
                    turn = c.execute(
                        "INSERT INTO conversation_turns(conversation_ref,sequence,message_ref,request_digest,user_text,payload,reply) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                        (
                            reference,
                            request.expected_sequence + 1,
                            request.message_ref,
                            request_hash,
                            redact_text(request.content),
                            Jsonb(payload),
                            reply,
                        ),
                    ).fetchone()
            payload = turn["payload"]
            try:
                task = None
                if payload["kind"] == "create":
                    task = self.backend.create_task(
                        actor,
                        TaskCreate(
                            client_request_ref="chat-"
                            + digest([reference, request.message_ref])[:40],
                            user_request=turn["user_text"],
                            context_task_ref=payload["context_task_ref"],
                        ),
                    )
                elif payload["kind"] == "replan":
                    task = self.backend.get_task(actor, payload["task_ref"])
                    if task["current_version"] == payload["expected_version"]:
                        task = self.backend.replan(
                            actor,
                            payload["task_ref"],
                            ReplanRequest(
                                expected_version=payload["expected_version"], steps=payload["steps"]
                            ),
                        )
                    else:
                        require(
                            task["current_version"] == payload["expected_version"] + 1
                            and same_steps(plan_steps(task), payload["steps"]),
                            "PLAN_VERSION_CONFLICT",
                            "任务计划已改变，请核对当前任务。",
                            409,
                        )
                if task:
                    with self.db.transaction() as c:
                        c.execute(
                            "UPDATE conversation_turns SET task_ref=%s WHERE conversation_ref=%s AND message_ref=%s",
                            (task["task_ref"], reference, request.message_ref),
                        )
                    if task["status"] not in {"WAITING_CLARIFICATION", "COMPLETED", "TERMINATED"}:
                        self.backend.run(actor, task["task_ref"])
            except ServiceError as error:
                if error.status >= 500 or error.code in {"TASK_BUSY"}:
                    raise
                with self.db.transaction() as c:
                    c.execute(
                        "UPDATE conversation_turns SET reply=%s WHERE conversation_ref=%s AND message_ref=%s",
                        ("未能推进：" + redact_text(str(error)), reference, request.message_ref),
                    )
            with self.db.transaction() as c:
                c.execute(
                    "UPDATE conversation_turns SET ready=true WHERE conversation_ref=%s AND message_ref=%s",
                    (reference, request.message_ref),
                )
        return self.get(actor, reference)
