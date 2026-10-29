"""持久化对话与任务关联；对话不能产生业务授权。"""

import re

from psycopg.types.json import Jsonb
from pydantic import Field

from finguard.backend.catalog import Constraints, ReplanRequest, RequestModel, TaskCreate, digest
from finguard.backend.chat import ConversationAI
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
    def __init__(self, backend, chat=None):
        self.chat = chat or ConversationAI()
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
                "SELECT sequence,message_ref,user_text,task_ref,ready,reply,created_at,payload->>'kind' AS kind,payload->>'error_code' AS error_code FROM conversation_turns WHERE conversation_ref=%s ORDER BY sequence",
                (reference,),
            ).fetchall()
        return {**row, "turns": turns}

    def _prepare(self, actor, content, previous, history=(), last=None, draft=None):
        if (
            previous
            and last
            and last["payload"]["kind"] in {"create", "replan"}
            and re.fullmatch(r"\s*(好[的]?|确认|继续|执行|可以)[。！!\s]*", content)
        ):
            return {"kind": "reply"}, "请使用当前操作卡片确认。对话文字不会签发付款授权。"
        if draft:
            if content.strip() in {"不用了", "取消这个目标", "放弃这个目标"}:
                return {"kind": "cancel_draft"}, "已放弃待澄清的目标，没有建立任务。"
            amount = re.fullmatch(
                r"(?:金额(?:是|为)?|补充金额[:：]?)?\s*([零〇一二两三四五六七八九十百千万\d.]+)\s*元",
                content.strip(),
            )
            if amount:
                goal, count = re.subn(
                    r"(转账|转|打款)(?=\s*(?:[，,；;]|$))",
                    lambda m: m[1] + amount[1] + "元",
                    draft["goal"],
                )
                if count == 1:
                    return self._task_intent(
                        actor,
                        goal,
                        previous,
                        Constraints.model_validate(draft.get("constraints", {})),
                    )
            if content.startswith(("完整目标：", "完整目标:")):
                return self._task_intent(
                    actor,
                    content[5:],
                    previous,
                    Constraints.model_validate(draft.get("constraints", {})),
                )
        match = re.fullmatch(
            r"\s*(?:改成|改为|金额改成|金额改为)\s*([零〇一二两三四五六七八九十百千万亿\d.]+)\s*元?[。！!\s]*",
            content,
        )
        if match:
            if last and last["payload"]["kind"] == "chat":
                return {
                    "kind": "reply"
                }, "请明确要修改哪项业务，或在任务卡片中修改；当前聊天不会直接改写此前转账。"
            if previous is None:
                return {"kind": "reply"}, "你希望修改哪项业务？请先说明完整目标，暂未建立任务。"
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
        if (
            previous
            and previous["status"] == "WAITING_CLARIFICATION"
            and (
                content.startswith(("完整目标：", "完整目标:"))
                or re.fullmatch(
                    r"(?:金额(?:是|为)?|补充金额[:：]?)?\s*([零〇一二两三四五六七八九十百千万\d.]+)\s*元",
                    content.strip(),
                )
            )
        ):
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
        kind = self.chat.decide(content, list(history))
        if kind == "chat":
            return {"kind": "chat"}, "正在回复…"
        if kind == "clarify":
            return {
                "kind": "draft",
                "goal": redact_text(content),
                "constraints": {},
            }, "你希望了解相关信息，还是让我办理业务？请明确目标，暂未建立任务。"
        return self._task_intent(actor, content, previous)

    def _task_intent(self, actor, goal, previous, constraints=None):
        context = self.backend._context(actor, goal)
        if previous:
            accounts = {
                o["arguments"].get("source_account_ref", o["arguments"].get("account_ref"))
                for o in previous["operations"]
            } - {None}
            if len(accounts) == 1:
                context["preferred_account_ref"] = next(iter(accounts))
        steps, limits, questions = self.backend.planner.infer(
            goal, context, constraints or Constraints()
        )
        if questions or not steps:
            return {
                "kind": "draft",
                "goal": redact_text(goal),
                "constraints": limits.model_dump(mode="json"),
            }, "暂未建立任务。" + "；".join(
                q["message"] for q in questions
            ) + " 可补充单笔金额，或以“完整目标：”重述全部目标。"
        return {
            "kind": "create",
            "goal": redact_text(goal),
            "context_task_ref": previous["task_ref"] if previous else None,
            "steps": [
                step.model_dump(mode="json") if hasattr(step, "model_dump") else step
                for step in steps
            ],
            "constraints": limits.model_dump(mode="json"),
        }, "已登记任务。查询自动推进，资金操作请核对确认卡。"

    def finish_chat(self, actor, reference, message_ref):
        with self.db.task_lock("chat-reply:" + reference) as acquired:
            if not acquired:
                return
            with self.db.transaction() as c:
                self._owner(c, actor, reference)
                turn = c.execute(
                    "SELECT * FROM conversation_turns WHERE conversation_ref=%s AND message_ref=%s",
                    (reference, message_ref),
                ).fetchone()
                if not turn or turn["ready"] or turn["payload"]["kind"] != "chat":
                    return
                rows = c.execute(
                    "SELECT user_text,reply,payload FROM conversation_turns WHERE conversation_ref=%s AND sequence<%s AND ready ORDER BY sequence DESC LIMIT 10",
                    (reference, turn["sequence"]),
                ).fetchall()
            history = []
            for row in reversed(rows):
                history.append({"role": "user", "content": row["user_text"]})
                if not row["payload"].get("error_code"):
                    history.append({"role": "assistant", "content": row["reply"]})
            output = ""
            stored = 0
            try:
                self.security.require_scope(actor, "tasks.read")
                for chunk in self.chat.stream(turn["user_text"], history):
                    output += chunk
                    require(
                        len(output) <= 20000,
                        "CHAT_RESPONSE_TOO_LARGE",
                        "AI 回复超过长度预算。",
                        503,
                    )
                    if len(output) - stored >= 80:
                        with self.db.transaction() as c:
                            self.security.require_scope(actor, "tasks.read", c)
                            c.execute(
                                "UPDATE conversation_turns SET reply=%s WHERE conversation_ref=%s AND message_ref=%s",
                                (redact_text(output), reference, message_ref),
                            )
                        stored = len(output)
                require(bool(output.strip()), "CHAT_EMPTY", "AI 未返回有效回复，请重试。", 503)
                reply = redact_text(output)
                payload = {"kind": "chat"}
            except Exception as error:
                code = error.code if isinstance(error, ServiceError) else "CHAT_UNAVAILABLE"
                reply = (
                    error.message
                    if isinstance(error, ServiceError)
                    else "AI 回复暂不可用，请重试。"
                )
                if output:
                    reply = redact_text(output) + "\n\n回复中断，请重试获取完整内容。"
                payload = {"kind": "chat", "error_code": code}
            with self.db.transaction() as c:
                c.execute(
                    "UPDATE conversation_turns SET reply=%s,payload=%s,ready=true WHERE conversation_ref=%s AND message_ref=%s",
                    (reply, Jsonb(payload), reference, message_ref),
                )

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
                    if (
                        turn["payload"]["kind"] == "chat"
                        and turn["payload"].get("error_code")
                        and last["sequence"] == turn["sequence"]
                    ):
                        with self.db.transaction() as c:
                            c.execute(
                                "UPDATE conversation_turns SET ready=false,reply='正在回复…',payload=%s WHERE conversation_ref=%s AND message_ref=%s",
                                (Jsonb({"kind": "chat"}), reference, request.message_ref),
                            )
                        return self.get(actor, reference)
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
                with self.db.transaction() as c:
                    recent = c.execute(
                        "SELECT user_text,reply,payload FROM conversation_turns WHERE conversation_ref=%s AND ready ORDER BY sequence DESC LIMIT 10",
                        (reference,),
                    ).fetchall()
                history = []
                for row in reversed(recent):
                    history.append({"role": "user", "content": row["user_text"]})
                    if not row["payload"].get("error_code"):
                        history.append({"role": "assistant", "content": row["reply"]})
                draft = None
                for row in recent:
                    if row["payload"]["kind"] in {"create", "replan", "cancel_draft"}:
                        break
                    if row["payload"]["kind"] == "draft":
                        draft = row["payload"]
                        break
                payload, reply = self._prepare(
                    actor, request.content, previous, history, last, draft
                )
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
            if payload["kind"] == "chat":
                return self.get(actor, reference)
            try:
                task = None
                if payload["kind"] == "create":
                    task = self.backend.create_task(
                        actor,
                        TaskCreate(
                            client_request_ref="chat-"
                            + digest([reference, request.message_ref])[:40],
                            user_request=payload.get("goal", turn["user_text"]),
                            context_task_ref=payload["context_task_ref"],
                            steps=payload.get("steps", ()),
                            constraints=payload.get("constraints", {}),
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
