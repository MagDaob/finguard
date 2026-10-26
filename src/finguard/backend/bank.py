"""模拟银行拥有独立提交事务，并以稳定操作编号保存权威业务结果。"""

from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from finguard.backend.catalog import ACTIONS, operation_digest
from finguard.backend.database import Database
from finguard.backend.errors import ServiceError, require
from finguard.domain.values import new_ref


class NotSubmitted(Exception):
    pass


class ResultUnknown(Exception):
    pass


class WorkerCrash(Exception):
    pass


class SimulatedBank:
    def __init__(self, database: Database):
        self.db = database

    def inject_fault(self, operation_ref: str, mode: str, count: int = 1) -> None:
        require(
            mode
            in {
                "before_send",
                "response_lost",
                "crash_before_bank",
                "crash_after_commit",
                "malformed_response",
                "lookup_unknown",
            },
            "INVALID_FAULT",
            "故障类型无效。",
        )
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO bank_faults(operation_ref,mode,remaining) VALUES (%s,%s,%s) ON CONFLICT(operation_ref) DO UPDATE SET mode=excluded.mode,remaining=excluded.remaining",
                (operation_ref, mode, count),
            )

    def _fault(self, operation_ref: str, lookup: bool = False) -> str | None:
        with self.db.transaction() as c:
            row = c.execute(
                "SELECT * FROM bank_faults WHERE operation_ref=%s AND remaining>0 FOR UPDATE",
                (operation_ref,),
            ).fetchone()
            if not row or (row["mode"] == "lookup_unknown") != lookup:
                return None
            c.execute(
                "UPDATE bank_faults SET remaining=remaining-1 WHERE operation_ref=%s",
                (operation_ref,),
            )
            return row["mode"]

    def list_accounts(self, subject: str) -> list[dict]:
        with self.db.transaction() as c:
            return c.execute(
                "SELECT account_ref,balance_minor,currency,active FROM bank_accounts WHERE subject_ref=%s ORDER BY account_ref",
                (subject,),
            ).fetchall()

    def list_recipients(self, subject: str) -> list[dict]:
        with self.db.transaction() as c:
            return c.execute(
                "SELECT recipient_ref,display_name,active FROM bank_recipients WHERE subject_ref=%s ORDER BY recipient_ref",
                (subject,),
            ).fetchall()

    def list_services(self, subject: str, kind: str) -> list[dict]:
        with self.db.transaction() as c:
            return c.execute(
                "SELECT resource_ref,state,data FROM bank_services WHERE subject_ref=%s AND kind=%s ORDER BY resource_ref",
                (subject, kind),
            ).fetchall()

    def list_products(self) -> list[dict]:
        with self.db.transaction() as c:
            return c.execute("SELECT * FROM bank_products ORDER BY product_ref").fetchall()

    def list_positions(self, subject: str) -> list[dict]:
        with self.db.transaction() as c:
            return c.execute(
                "SELECT p.account_ref,p.product_ref,p.value_minor FROM bank_positions p JOIN bank_accounts a USING(account_ref) WHERE a.subject_ref=%s ORDER BY p.account_ref,p.product_ref",
                (subject,),
            ).fetchall()

    def preview(self, subject: str, action: str, arguments: dict) -> dict:
        with self.db.transaction() as c:
            return self._validate(c, subject, action, arguments)

    def _account(self, c, subject: str, account_ref: str) -> dict:
        row = c.execute(
            "SELECT * FROM bank_accounts WHERE account_ref=%s AND subject_ref=%s",
            (account_ref, subject),
        ).fetchone()
        require(row is not None, "RESOURCE_NOT_FOUND", "账户不存在或不可访问。", 404)
        require(row["active"], "ACCOUNT_UNAVAILABLE", "账户不可用。", 409)
        return row

    def _validate(self, c, subject: str, action: str, args: dict) -> dict:
        require(action in ACTIONS, "UNKNOWN_TOOL", "工具不存在。", 422)
        account_ref = args.get("source_account_ref", args.get("account_ref"))
        account = self._account(c, subject, account_ref) if account_ref else None
        details = {"action": action, "arguments": args, "simulation": True}
        if account:
            details["available_balance_minor"] = account["balance_minor"]
        if action == "transfer.create":
            recipient = c.execute(
                "SELECT * FROM bank_recipients WHERE recipient_ref=%s AND subject_ref=%s AND active",
                (args["recipient_ref"], subject),
            ).fetchone()
            require(recipient is not None, "RECIPIENT_NOT_FOUND", "收款人不存在或尚未确认。", 404)
            require(
                recipient["destination_account_ref"] != account_ref,
                "SAME_ACCOUNT",
                "付款账户与收款账户不能相同。",
                409,
            )
            details["recipient_name"] = recipient["display_name"]
        if action.startswith("product."):
            product = c.execute(
                "SELECT * FROM bank_products WHERE product_ref=%s", (args["product_ref"],)
            ).fetchone()
            require(
                product is not None and product["trading_open"],
                "PRODUCT_UNAVAILABLE",
                "产品不存在或当前不接受交易。",
                409,
            )
            risk = c.execute(
                "SELECT risk_level FROM principals WHERE subject_ref=%s", (subject,)
            ).fetchone()["risk_level"]
            if action == "product.subscribe":
                require(risk >= product["risk_level"], "RISK_MISMATCH", "风险等级不匹配。", 409)
            else:
                position = c.execute(
                    "SELECT value_minor FROM bank_positions WHERE account_ref=%s AND product_ref=%s",
                    (account_ref, args["product_ref"]),
                ).fetchone()
                require(
                    position is not None and position["value_minor"] >= args["amount_minor"],
                    "INSUFFICIENT_POSITION",
                    "可赎回份额不足。",
                    409,
                )
                details["settlement_delay_seconds"] = product["settlement_delay_seconds"]
        if ACTIONS[action].monetary:
            require(
                args["fee_minor"] == 0,
                "QUOTE_CHANGED",
                "当前模拟报价费用为零，请更新操作预览。",
                409,
            )
        if action in {"subscription.cancel", "card.set_lock"}:
            kind = "card" if action == "card.set_lock" else "subscription"
            key = args["card_ref"] if kind == "card" else args["subscription_ref"]
            resource = c.execute(
                "SELECT state,data FROM bank_services WHERE resource_ref=%s AND subject_ref=%s AND kind=%s",
                (key, subject, kind),
            ).fetchone()
            require(resource is not None, "RESOURCE_NOT_FOUND", "服务不存在或不可访问。", 404)
            details["current_state"] = resource["state"]
        return details

    def execute(self, subject: str, operation_ref: str, action: str, arguments: dict) -> dict:
        fault = self._fault(operation_ref)
        if fault == "before_send":
            raise NotSubmitted()
        if fault == "crash_before_bank":
            raise WorkerCrash("模拟进程在银行调用前退出。")
        fingerprint = operation_digest(subject, action, arguments)
        with self.db.transaction() as c:
            c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"bank:{operation_ref}",)
            )
            existing = c.execute(
                "SELECT * FROM bank_commands WHERE operation_ref=%s FOR UPDATE", (operation_ref,)
            ).fetchone()
            if existing:
                require(
                    existing["digest"] == fingerprint and existing["subject_ref"] == subject,
                    "OPERATION_CONFLICT",
                    "操作编号已绑定其他业务参数。",
                    409,
                )
                result = self._receipt(existing)
            else:
                c.execute(
                    "INSERT INTO bank_commands(operation_ref,subject_ref,action,arguments,digest,status,result) VALUES (%s,%s,%s,%s,%s,'PENDING','{}')",
                    (operation_ref, subject, action, Jsonb(arguments), fingerprint),
                )
                try:
                    with c.transaction():
                        details = self._apply(c, subject, operation_ref, action, arguments)
                    status = details.pop("status", "SUCCEEDED")
                except ServiceError as exc:
                    details = {"error_code": exc.code, "message": exc.message}
                    status = "FAILED"
                settle_at = details.pop("settle_at", None)
                c.execute(
                    "UPDATE bank_commands SET status=%s,result=%s,settle_at=%s WHERE operation_ref=%s",
                    (status, Jsonb(details), settle_at, operation_ref),
                )
                result = {
                    "operation_ref": operation_ref,
                    "transaction_ref": operation_ref,
                    "status": status,
                    **details,
                }
        if fault == "response_lost":
            raise ResultUnknown()
        if fault == "crash_after_commit":
            raise WorkerCrash("模拟进程在银行生效后退出。")
        if fault == "malformed_response":
            return {"status": "SUCCEEDED"}
        return result

    @staticmethod
    def _receipt(row: dict) -> dict:
        return {
            "operation_ref": row["operation_ref"],
            "transaction_ref": row["operation_ref"],
            "status": row["status"],
            **row["result"],
        }

    def _ledger(self, c, operation: str, account: str, amount: int, category: str) -> str:
        reference = new_ref("entry")
        c.execute(
            "INSERT INTO bank_ledger(entry_ref,operation_ref,account_ref,amount_minor,category) VALUES (%s,%s,%s,%s,%s)",
            (reference, operation, account, amount, category),
        )
        return reference

    def _apply(self, c, subject: str, operation: str, action: str, args: dict) -> dict:
        # 先锁定会修改的资源，再复检业务条件，避免预览与提交之间的条件变化。
        if action in {"transfer.create", "product.subscribe", "product.redeem"}:
            account_ref = args.get("source_account_ref", args.get("account_ref"))
            refs = [account_ref]
            if action == "transfer.create":
                recipient = c.execute(
                    "SELECT destination_account_ref FROM bank_recipients WHERE recipient_ref=%s AND subject_ref=%s AND active",
                    (args["recipient_ref"], subject),
                ).fetchone()
                require(recipient is not None, "RECIPIENT_NOT_FOUND", "收款人不可用。", 404)
                refs.append(recipient["destination_account_ref"])
            c.execute(
                "SELECT account_ref FROM bank_accounts WHERE account_ref=ANY(%s) ORDER BY account_ref FOR UPDATE",
                (refs,),
            ).fetchall()
        elif action in {"card.set_lock", "subscription.cancel"}:
            resource = args.get("card_ref", args.get("subscription_ref"))
            c.execute(
                "SELECT resource_ref FROM bank_services WHERE resource_ref=%s FOR UPDATE",
                (resource,),
            ).fetchone()
        details = self._validate(c, subject, action, args)
        if action == "accounts.balance":
            return {
                "account_ref": args["account_ref"],
                "balance_minor": details["available_balance_minor"],
                "currency": "CNY",
                "funds_available": True,
            }
        if action == "bills.analyze":
            rows = c.execute(
                "SELECT amount_minor,category FROM bank_ledger WHERE account_ref=%s AND occurred_at>=now() - make_interval(days => %s) AND (category LIKE 'spend:%%' OR category LIKE 'refund:%%')",
                (args["account_ref"], args["days"]),
            ).fetchall()
            spending = sum(-row["amount_minor"] for row in rows if row["amount_minor"] < 0)
            refunds = sum(row["amount_minor"] for row in rows if row["amount_minor"] > 0)
            categories = {}
            for row in rows:
                category = row["category"].split(":", 1)[1]
                categories[category] = categories.get(category, 0) - row["amount_minor"]
            return {
                "account_ref": args["account_ref"],
                "days": args["days"],
                "spending_minor": spending,
                "refunds_minor": refunds,
                "net_spending_minor": spending - refunds,
                "categories": categories,
                "currency": "CNY",
                "basis": "POSTED_CASH_FLOW",
            }
        if action == "card.set_lock":
            state = "LOCKED" if args["locked"] else "ACTIVE"
            c.execute(
                "UPDATE bank_services SET state=%s WHERE resource_ref=%s", (state, args["card_ref"])
            )
            return {
                "card_ref": args["card_ref"],
                "state": state,
                "previous_state": details["current_state"],
                "effect": "TEMPORARY_LOCK",
            }
        if action == "subscription.cancel":
            c.execute(
                "UPDATE bank_services SET state='CANCELLED' WHERE resource_ref=%s",
                (args["subscription_ref"],),
            )
            return {
                "subscription_ref": args["subscription_ref"],
                "contract_status": "CANCELLED",
                "effective_at": datetime.now(UTC).isoformat(),
                "refund_minor": 0,
                "auto_debit_mandate_changed": False,
            }
        account_ref = args.get("source_account_ref", args.get("account_ref"))
        account = self._account(c, subject, account_ref)
        amount = args["amount_minor"]
        entries = []
        if action in {"transfer.create", "product.subscribe"}:
            remaining = account["balance_minor"] - amount - args["fee_minor"]
            require(
                remaining >= args["minimum_remaining_balance_minor"],
                "INSUFFICIENT_FUNDS",
                "余额不足或不满足最低保留金额。",
                409,
            )
            c.execute(
                "UPDATE bank_accounts SET balance_minor=%s WHERE account_ref=%s",
                (remaining, account_ref),
            )
            entries.append(self._ledger(c, operation, account_ref, -amount, action))
            if action == "transfer.create":
                recipient_account = refs[1]
                destination = c.execute(
                    "SELECT * FROM bank_accounts WHERE account_ref=%s", (recipient_account,)
                ).fetchone()
                require(
                    destination["active"] and destination["balance_minor"] + amount <= 2**63 - 1,
                    "RECIPIENT_UNAVAILABLE",
                    "收款账户不可用。",
                    409,
                )
                c.execute(
                    "UPDATE bank_accounts SET balance_minor=balance_minor+%s WHERE account_ref=%s",
                    (amount, recipient_account),
                )
                self._ledger(c, operation, recipient_account, amount, "transfer.credit")
            else:
                position = c.execute(
                    "SELECT value_minor FROM bank_positions WHERE account_ref=%s AND product_ref=%s",
                    (account_ref, args["product_ref"]),
                ).fetchone()
                reserved = c.execute(
                    "SELECT COALESCE(sum((arguments->>'amount_minor')::bigint),0) AS n FROM bank_commands WHERE action='product.redeem' AND status='PENDING' AND arguments->>'account_ref'=%s AND arguments->>'product_ref'=%s",
                    (account_ref, args["product_ref"]),
                ).fetchone()["n"]
                require(
                    (position["value_minor"] if position else 0) + reserved + amount <= 2**63 - 1,
                    "POSITION_OVERFLOW",
                    "持仓金额超出存储范围。",
                    409,
                )
                c.execute(
                    "INSERT INTO bank_positions(account_ref,product_ref,value_minor) VALUES (%s,%s,%s) ON CONFLICT(account_ref,product_ref) DO UPDATE SET value_minor=bank_positions.value_minor+excluded.value_minor",
                    (account_ref, args["product_ref"], amount),
                )
            return {
                "account_ref": account_ref,
                "amount_minor": amount,
                "balance_after_minor": remaining,
                "ledger_entry_refs": entries,
                "currency": "CNY",
                "funds_available": True,
            }
        require(
            account["balance_minor"] + amount <= 2**63 - 1,
            "BALANCE_OVERFLOW",
            "账户金额超出存储范围。",
            409,
        )
        require(
            account["balance_minor"] + amount >= args["minimum_remaining_balance_minor"],
            "INSUFFICIENT_FUNDS",
            "赎回后仍不满足最低保留金额。",
            409,
        )
        c.execute(
            "UPDATE bank_positions SET value_minor=value_minor-%s WHERE account_ref=%s AND product_ref=%s",
            (amount, account_ref, args["product_ref"]),
        )
        return {
            "status": "PENDING",
            "account_ref": account_ref,
            "amount_minor": amount,
            "currency": "CNY",
            "funds_available": False,
            "settle_at": datetime.now(UTC) + timedelta(seconds=details["settlement_delay_seconds"]),
        }

    def lookup(
        self,
        subject: str,
        operation: str,
        fingerprint: str,
        fence_if_absent: bool = False,
        action: str | None = None,
        arguments: dict | None = None,
    ) -> dict | None:
        if self._fault(operation, lookup=True):
            return None
        with self.db.transaction() as c:
            c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"bank:{operation}",)
            )
            row = c.execute(
                "SELECT * FROM bank_commands WHERE operation_ref=%s FOR UPDATE", (operation,)
            ).fetchone()
            if row is None and fence_if_absent:
                # 此能力仅属于模拟适配器：写入失败记录，阻止迟到的原请求生效。
                require(
                    action is not None and arguments is not None,
                    "FENCE_CONTEXT_MISSING",
                    "核实缺少操作上下文。",
                )
                result = {
                    "error_code": "NOT_SUBMITTED",
                    "message": "已确认没有记账，并阻止迟到请求执行。",
                }
                c.execute(
                    "INSERT INTO bank_commands(operation_ref,subject_ref,action,arguments,digest,status,result) VALUES (%s,%s,%s,%s,%s,'FAILED',%s)",
                    (operation, subject, action, Jsonb(arguments), fingerprint, Jsonb(result)),
                )
                return {
                    "operation_ref": operation,
                    "transaction_ref": operation,
                    "status": "FAILED",
                    **result,
                }
            if row is None:
                return None
            require(
                row["subject_ref"] == subject and row["digest"] == fingerprint,
                "OPERATION_CONFLICT",
                "查询操作与业务记录不匹配。",
                409,
            )
            if (
                row["status"] == "PENDING"
                and row["settle_at"]
                and row["settle_at"] <= datetime.now(UTC)
            ):
                account_ref, amount = (
                    row["arguments"]["account_ref"],
                    row["arguments"]["amount_minor"],
                )
                c.execute(
                    "SELECT account_ref FROM bank_accounts WHERE account_ref=%s FOR UPDATE",
                    (account_ref,),
                ).fetchone()
                account = c.execute(
                    "SELECT * FROM bank_accounts WHERE account_ref=%s AND subject_ref=%s",
                    (account_ref, subject),
                ).fetchone()
                balance = account["balance_minor"] + amount
                error = None
                if not account["active"]:
                    error = "ACCOUNT_UNAVAILABLE"
                elif balance > 2**63 - 1:
                    error = "BALANCE_OVERFLOW"
                elif balance < row["arguments"]["minimum_remaining_balance_minor"]:
                    error = "INSUFFICIENT_FUNDS"
                if error:
                    c.execute(
                        "UPDATE bank_positions SET value_minor=value_minor+%s WHERE account_ref=%s AND product_ref=%s",
                        (amount, account_ref, row["arguments"]["product_ref"]),
                    )
                    result = {
                        "error_code": error,
                        "position_restored": True,
                        "funds_available": False,
                    }
                    c.execute(
                        "UPDATE bank_commands SET status='FAILED',result=%s,settle_at=NULL WHERE operation_ref=%s",
                        (Jsonb(result), operation),
                    )
                    return self._receipt({**row, "status": "FAILED", "result": result})
                c.execute(
                    "UPDATE bank_accounts SET balance_minor=%s WHERE account_ref=%s",
                    (balance, account_ref),
                )
                entry = self._ledger(c, operation, account_ref, amount, "product.redeem")
                result = {
                    "account_ref": account_ref,
                    "amount_minor": amount,
                    "balance_after_minor": balance,
                    "ledger_entry_refs": [entry],
                    "currency": "CNY",
                    "funds_available": True,
                }
                c.execute(
                    "UPDATE bank_commands SET status='SUCCEEDED',result=%s,settle_at=NULL WHERE operation_ref=%s",
                    (Jsonb(result), operation),
                )
                row = {**row, "status": "SUCCEEDED", "result": result}
            return self._receipt(row)

    def verify(
        self, subject: str, operation: str, fingerprint: str
    ) -> tuple[dict | None, list[dict]]:
        result = self.lookup(subject, operation, fingerprint)
        if not result or result["status"] != "SUCCEEDED":
            return result, []
        evidence = [{"kind": "business_record", "record_ref": operation, "data": result}]
        with self.db.transaction() as c:
            command = c.execute(
                "SELECT action,arguments FROM bank_commands WHERE operation_ref=%s AND subject_ref=%s",
                (operation, subject),
            ).fetchone()
            if ACTIONS[command["action"]].monetary:
                refs = result.get("ledger_entry_refs", [])
                rows = c.execute(
                    "SELECT entry_ref,amount_minor FROM bank_ledger WHERE operation_ref=%s AND account_ref=%s",
                    (operation, result["account_ref"]),
                ).fetchall()
                expected_amount = command["arguments"]["amount_minor"] * (
                    1 if command["action"] == "product.redeem" else -1
                )
                require(
                    len(rows) == 1
                    and rows[0]["entry_ref"] in refs
                    and rows[0]["amount_minor"] == expected_amount,
                    "EVIDENCE_MISMATCH",
                    "流水与业务结果不一致。",
                    409,
                )
                evidence.extend(
                    [
                        {
                            "kind": "ledger_entry",
                            "record_ref": rows[0]["entry_ref"],
                            "data": {"amount_minor": rows[0]["amount_minor"]},
                        },
                        {
                            "kind": "balance_snapshot",
                            "record_ref": operation,
                            "data": {
                                "balance_after_minor": result["balance_after_minor"],
                                "currency": "CNY",
                            },
                        },
                    ]
                )
        return result, evidence
