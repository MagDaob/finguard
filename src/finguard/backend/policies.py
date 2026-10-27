"""离线提示词候选需要对照评估与显式审核后才能发布。"""

from psycopg.types.json import Jsonb

from finguard.backend.catalog import canonical, digest
from finguard.backend.database import Database
from finguard.backend.errors import require
from finguard.backend.evaluation import ReplayRunner, ReplaySuite
from finguard.backend.planning import BaselinePlanner, GatewayPlanner
from finguard.backend.security import Actor, Security
from finguard.domain.values import new_ref


def clone_gateway(gateway: GatewayPlanner, prompt: str | None) -> GatewayPlanner:
    return gateway.with_prompt(prompt)


class PolicyPlanner(BaselinePlanner):
    def __init__(self, database: Database, gateway: GatewayPlanner):
        self.db, self.gateway = database, gateway

    def diagnostics(self):
        return self.gateway.diagnostics() if hasattr(self.gateway, "diagnostics") else None

    def propose(self, goals, constraints):
        with self.db.transaction() as c:
            row = c.execute(
                "SELECT prompt FROM optimization_candidates WHERE status='RELEASED'"
            ).fetchone()
        return clone_gateway(self.gateway, row["prompt"] if row else self.gateway.prompt).propose(
            goals, constraints
        )


class Policies:
    def __init__(self, database: Database, gateway: GatewayPlanner | None):
        self.db, self.gateway, self.security = database, gateway, Security(database)

    def create(self, actor: Actor, prompt: str) -> dict:
        self.security.require_scope(actor, "evaluation.manage")
        require(1 <= len(prompt) <= 8000, "INVALID_PROMPT", "提示词长度无效。", 422)
        reference = new_ref("candidate")
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO optimization_candidates(candidate_ref,created_by,prompt,status) VALUES (%s,%s,%s,'DRAFT')",
                (reference, actor.subject_ref, prompt),
            )
        return {"candidate_ref": reference, "status": "DRAFT"}

    def propose_from_failures(self, actor: Actor, suite: ReplaySuite) -> dict:
        self.security.require_scope(actor, "evaluation.manage")
        report = ReplayRunner(self.db).run(
            suite, PolicyPlanner(self.db, self.gateway) if self.gateway else None
        )
        failures = [
            {
                "case_ref": item["case_ref"],
                "layer": item["failure_layer"],
                "error_code": item["error_code"],
            }
            for item in report["cases"]
            if not item["passed"]
        ]
        require(bool(failures), "NO_FAILURES", "回放没有失败，无需生成失败改进候选。", 409)
        prompt = (
            "Review candidate ordering and dependency choices against these synthetic replay failures. Preserve every goal, exact arguments, required dependencies and funds conditions. Never create authorizations or request broader access. If the failure is in control, persistence or evidence, report it for engineering review rather than weaken checks. Failures: "
            + canonical(failures)
        )
        candidate = self.create(actor, prompt)
        return {
            **candidate,
            "failure_analysis": failures,
            "source_report": report,
            "requires_evaluation": True,
        }

    def evaluate(self, actor: Actor, reference: str, suite: ReplaySuite) -> dict:
        self.security.require_scope(actor, "evaluation.manage")
        with self.db.transaction() as c:
            candidate = c.execute(
                "SELECT * FROM optimization_candidates WHERE candidate_ref=%s", (reference,)
            ).fetchone()
            require(candidate is not None, "CANDIDATE_NOT_FOUND", "候选不存在。", 404)
            require(
                candidate["status"] != "RELEASED",
                "CANDIDATE_ACTIVE",
                "已发布候选不能原地改写评估。",
                409,
            )
            active = c.execute(
                "SELECT candidate_ref,prompt FROM optimization_candidates WHERE status='RELEASED'"
            ).fetchone()
        runner = ReplayRunner(self.db)
        baseline = runner.run(
            suite,
            clone_gateway(self.gateway, active["prompt"] if active else None)
            if self.gateway
            else None,
        )
        proposed = runner.run(
            suite, clone_gateway(self.gateway, candidate["prompt"]) if self.gateway else None
        )
        legal = proposed["business_completion_rate"]
        eligible = bool(
            self.gateway
            and proposed["passed"]
            and baseline["passed"]
            and proposed["case_count"] >= 2
            and legal is not None
            and legal >= baseline["business_completion_rate"]
            and proposed["correct_handling_rate"] >= baseline["correct_handling_rate"]
        )
        report = {
            "baseline": baseline,
            "candidate": proposed,
            "eligible_for_release": eligible,
            "baseline_candidate_ref": active["candidate_ref"] if active else None,
            "prompt_digest": digest(candidate["prompt"]),
        }
        report["evaluation_digest"] = digest(report)
        with self.db.transaction() as c:
            changed = c.execute(
                "UPDATE optimization_candidates SET evaluation=%s,status='EVALUATED' WHERE candidate_ref=%s AND status<>'RELEASED' RETURNING candidate_ref",
                (Jsonb(report), reference),
            ).fetchone()
            require(changed is not None, "CANDIDATE_ACTIVE", "候选状态已变化。", 409)
        return report

    def release(
        self, actor: Actor, reference: str, expected_evaluation_digest: str, reviewed: bool
    ) -> dict:
        self.security.require_scope(actor, "evaluation.manage")
        require(reviewed is True, "REVIEW_REQUIRED", "需要明确审核评估结果。", 422)
        with self.db.transaction() as c:
            c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"policy:{self.db.schema}",),
            )
            candidate = c.execute(
                "SELECT * FROM optimization_candidates WHERE candidate_ref=%s FOR UPDATE",
                (reference,),
            ).fetchone()
            require(candidate is not None, "CANDIDATE_NOT_FOUND", "候选不存在。", 404)
            report = candidate["evaluation"]
            require(
                candidate["status"] in {"EVALUATED", "ROLLED_BACK"}
                and report
                and report["eligible_for_release"]
                and report["evaluation_digest"] == expected_evaluation_digest,
                "EVALUATION_REQUIRED",
                "候选缺少适用且通过的对照评估。",
                409,
            )
            active = c.execute(
                "SELECT candidate_ref FROM optimization_candidates WHERE status='RELEASED' FOR UPDATE"
            ).fetchone()
            previous = active["candidate_ref"] if active else None
            require(
                previous == report["baseline_candidate_ref"],
                "BASELINE_CHANGED",
                "当前基线已变化，需要重新评估。",
                409,
            )
            c.execute(
                "UPDATE optimization_candidates SET status='ROLLED_BACK' WHERE status='RELEASED'"
            )
            c.execute(
                "UPDATE optimization_candidates SET status='RELEASED' WHERE candidate_ref=%s",
                (reference,),
            )
            c.execute(
                "INSERT INTO policy_events(candidate_ref,previous_ref,event,actor_ref) VALUES (%s,%s,'RELEASE',%s)",
                (reference, previous, actor.subject_ref),
            )
        return {"candidate_ref": reference, "status": "RELEASED", "previous_ref": previous}

    def rollback(self, actor: Actor, reference: str) -> dict:
        self.security.require_scope(actor, "evaluation.manage")
        with self.db.transaction() as c:
            c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"policy:{self.db.schema}",),
            )
            current = c.execute(
                "SELECT candidate_ref FROM optimization_candidates WHERE status='RELEASED' FOR UPDATE"
            ).fetchone()
            require(
                current is not None and current["candidate_ref"] == reference,
                "ACTIVE_POLICY_CHANGED",
                "当前发布版本已变化。",
                409,
            )
            history = c.execute(
                "SELECT previous_ref FROM policy_events WHERE candidate_ref=%s AND event='RELEASE' ORDER BY sequence DESC LIMIT 1",
                (reference,),
            ).fetchone()
            previous = history["previous_ref"] if history else None
            c.execute(
                "UPDATE optimization_candidates SET status='ROLLED_BACK' WHERE candidate_ref=%s",
                (reference,),
            )
            if previous:
                c.execute(
                    "UPDATE optimization_candidates SET status='RELEASED' WHERE candidate_ref=%s",
                    (previous,),
                )
            c.execute(
                "INSERT INTO policy_events(candidate_ref,previous_ref,event,actor_ref) VALUES (%s,%s,'ROLLBACK',%s)",
                (reference, previous, actor.subject_ref),
            )
        return {"candidate_ref": reference, "status": "ROLLED_BACK", "restored_ref": previous}
