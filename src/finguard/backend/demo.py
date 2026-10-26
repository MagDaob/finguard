"""可重复导入的虚构业务状态；不会覆盖已有账户余额。"""

from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from finguard.backend.database import Database
from finguard.backend.security import Security


def seed_demo(database: Database) -> None:
    security = Security(database)
    for subject, name, risk in (
        ("user-demo-001", "演示用户", 2),
        ("user-demo-002", "演示收款人", 1),
        ("operator-demo", "演示维护员", 1),
    ):
        security.provision(subject, name, risk)
    with database.transaction() as c:
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"seed:{database.schema}",)
        )
        for account, subject, balance in (
            ("account-demo-001", "user-demo-001", 155000),
            ("account-demo-002", "user-demo-002", 20000),
        ):
            c.execute(
                "INSERT INTO bank_accounts(account_ref,subject_ref,balance_minor,opening_balance_minor,currency) VALUES (%s,%s,%s,%s,'CNY') ON CONFLICT DO NOTHING",
                (account, subject, balance, balance),
            )
        for reference, subject, name, target in (
            ("recipient-demo-wang", "user-demo-001", "小王", "account-demo-002"),
            ("recipient-demo-li", "user-demo-002", "小李", "account-demo-001"),
        ):
            c.execute(
                "INSERT INTO bank_recipients(recipient_ref,subject_ref,display_name,destination_account_ref) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (reference, subject, name, target),
            )
        for reference, name, risk in (
            ("fund-demo", "模拟稳健产品", 1),
            ("fund-high-risk", "模拟高风险产品", 5),
        ):
            c.execute(
                "INSERT INTO bank_products(product_ref,name,risk_level,settlement_delay_seconds) VALUES (%s,%s,%s,5) ON CONFLICT DO NOTHING",
                (reference, name, risk),
            )
        c.execute(
            "INSERT INTO bank_positions(account_ref,product_ref,value_minor) VALUES ('account-demo-001','fund-demo',30000) ON CONFLICT DO NOTHING"
        )
        for reference, kind, data in (
            ("card-demo", "card", {"account_ref": "account-demo-001"}),
            (
                "subscription-demo",
                "subscription",
                {"merchant": "模拟音乐服务", "period_price_minor": 1500, "currency": "CNY"},
            ),
        ):
            c.execute(
                "INSERT INTO bank_services(resource_ref,subject_ref,kind,state,data) VALUES (%s,'user-demo-001',%s,'ACTIVE',%s) ON CONFLICT DO NOTHING",
                (reference, kind, Jsonb(data)),
            )
        for label, amount, category, refund in (
            ("groceries", -4000, "spend:groceries", None),
            ("subscription", -1500, "spend:subscription", None),
            ("refund", 500, "refund:groceries", "seed-entry-groceries"),
        ):
            operation = f"seed-{label}"
            inserted = c.execute(
                "INSERT INTO bank_commands(operation_ref,subject_ref,action,arguments,digest,status,result) VALUES (%s,'user-demo-001','seed.history','{}','seed','SUCCEEDED','{}') ON CONFLICT DO NOTHING RETURNING operation_ref",
                (operation,),
            ).fetchone()
            if inserted:
                c.execute(
                    "INSERT INTO bank_ledger(entry_ref,operation_ref,account_ref,amount_minor,category,refund_of,occurred_at) VALUES (%s,%s,'account-demo-001',%s,%s,%s,%s)",
                    (
                        f"seed-entry-{label}",
                        operation,
                        amount,
                        category,
                        refund,
                        datetime.now(UTC) - timedelta(days=1),
                    ),
                )
                c.execute(
                    "UPDATE bank_accounts SET balance_minor=balance_minor+%s WHERE account_ref='account-demo-001'",
                    (amount,),
                )
