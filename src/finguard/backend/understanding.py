"""逐句完整匹配的有限语法；未理解片段不能变成可执行的部分计划。"""

import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from finguard.backend.catalog import Constraints, Step
from finguard.backend.errors import require

CHINA_TZ = timezone(timedelta(hours=8))
NUMBER = r"(?:[0-9]+(?:\.[0-9]+)?|[零一二两三四五六七八九十百千万点]+)"
AMOUNT = rf"(?P<amount>{NUMBER})\s*(?:元|块钱|块)?"
DIGITS = "零一二三四五六七八九"


def _render(number: int) -> str:
    if number == 0:
        return "零"
    if number >= 10000:
        high, low = divmod(number, 10000)
        return _render(high) + "万" + (("零" if low < 1000 else "") + _render(low) if low else "")
    result, pending = "", False
    for unit, label in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        digit, number = divmod(number, unit)
        if digit:
            result += ("零" if pending else "") + DIGITS[digit] + label
            pending = False
        elif result and number:
            pending = True
    return result[1:] if result.startswith("一十") else result


def money_minor(text: str) -> int:
    value = text.replace("两", "二")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        parts = value.split("点")
        require(len(parts) <= 2, "INVALID_AMOUNT", "金额表达不明确。", 422)
        integer = parts[0]
        total, section, digit = 0, 0, 0
        for char in integer:
            if char in DIGITS:
                digit = DIGITS.index(char)
            elif char in "十百千":
                section += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[char]
                digit = 0
            elif char == "万":
                total += (section + digit) * 10000
                section, digit = 0, 0
            else:
                require(False, "INVALID_AMOUNT", "金额表达不明确。", 422)
        number = total + section + digit
        require(_render(number) == integer, "INVALID_AMOUNT", "请使用完整、无歧义的金额。", 422)
        fraction = ""
        if len(parts) == 2:
            require(
                1 <= len(parts[1]) <= 2 and all(c in DIGITS for c in parts[1]),
                "INVALID_AMOUNT",
                "金额最多包含两位小数。",
                422,
            )
            fraction = "." + "".join(str(DIGITS.index(c)) for c in parts[1])
        value = str(number) + fraction
    amount = Decimal(value)
    require(
        0 < amount <= Decimal(2**63 - 1) / 100 and amount.as_tuple().exponent >= -2,
        "INVALID_AMOUNT",
        "金额必须为正数，且最多包含两位小数。",
        422,
    )
    return int(amount * 100)


def infer(message: str, context: dict, constraints: Constraints):
    def question(field, text, options=()):
        return (), constraints, [{"field": field, "message": text, "options": list(options)}]

    if re.search(r"不要|不转|别|禁止|撤销|如果|除非|(?<!到)超过|忽略|跳过|绕过", message):
        return question("goals", "请求含否定、未支持的条件或控制指令，请确认结构化目标。")
    accounts = [a for a in context.get("accounts", []) if a["active"]]
    preferred = context.get("preferred_account_ref")
    if preferred:
        accounts = [a for a in accounts if a["account_ref"] == preferred]
    if len(accounts) != 1:
        return question(
            "account_ref", "请选择付款或分析账户。", [a["account_ref"] for a in accounts]
        )
    account = accounts[0]["account_ref"]
    text = message.strip().rstrip("。！？!? ")
    reserve_pattern = rf"(?:至少保留|至少留|余额不少于)\s*({NUMBER})\s*(?:元|块钱|块)"
    reserves = list(re.finditer(reserve_pattern, text))
    if reserves:
        constraints = Constraints(
            minimum_remaining_balance_minor=max(
                constraints.minimum_remaining_balance_minor,
                *(money_minor(m[1]) for m in reserves),
            ),
            maximum_fee_minor=constraints.maximum_fee_minor,
        )
        text = re.sub(reserve_pattern, "", text)
    clauses = [
        c.strip()
        for c in re.split(r"[，,；;。\n]+|然后|接着|并且|以及|到账后|再", text)
        if c.strip()
    ]
    if not clauses:
        return question("goals", "请提供明确的业务目标。")
    if len(clauses) > 32:
        return question("goals", "目标过多，请使用最多 32 个节点的结构化计划。")
    steps = []
    today = context.get("today") or datetime.now(UTC).astimezone(CHINA_TZ).date()
    for original in clauses:
        clause = re.sub(r"^(?:请|帮我|麻烦|先|再|并|和)+", "", original).strip()
        if re.search(
            rf"(?:取消|订阅|余额|账单|锁卡|锁定卡|解锁卡|{NUMBER}\s*(?:元|块钱|块))[吧吗]$", clause
        ):
            clause = clause[:-1]
        step = None
        if re.fullmatch(
            r"(?:查(?:询|一下)?|看(?:看|一下)?|查询一下)?(?:我(?:的)?|账户(?:的)?)?(?:当前)?余额",
            clause,
        ):
            step = Step(
                step_ref="balance", action="accounts.balance", arguments={"account_ref": account}
            )
        elif re.fullmatch(r"还剩多少(?:钱|元)?|剩余多少(?:钱|元)?", clause):
            if not preferred:
                return question("context_task_ref", "请关联此前任务或明确要查询的账户。")
            step = Step(
                step_ref="balance", action="accounts.balance", arguments={"account_ref": account}
            )
        else:
            transfer = re.fullmatch(rf"(?:给|向)(?P<payee>.+?)\s*(?:转账?|打款)\s*{AMOUNT}", clause)
            if not transfer:
                transfer = re.fullmatch(
                    rf"(?:转账?|打款)\s*(?:给|向)(?P<payee>.+?)\s*{AMOUNT}", clause
                )
            if not transfer:
                transfer = re.fullmatch(
                    rf"(?:转账?|打款)\s*{AMOUNT}\s*(?:给|向)(?P<payee>.+)", clause
                )
            if transfer:
                people = [
                    r
                    for r in context.get("recipients", [])
                    if r["active"] and r["display_name"] == transfer["payee"].strip()
                ]
                if len(people) != 1:
                    return question(
                        "recipient_ref",
                        "请确认具体收款人。",
                        [r["recipient_ref"] for r in context.get("recipients", []) if r["active"]],
                    )
                step = Step(
                    step_ref="transfer",
                    action="transfer.create",
                    arguments={
                        "source_account_ref": account,
                        "recipient_ref": people[0]["recipient_ref"],
                        "amount_minor": money_minor(transfer["amount"]),
                        "currency": "CNY",
                        "fee_minor": 0,
                        "minimum_remaining_balance_minor": constraints.minimum_remaining_balance_minor,
                    },
                )
        if step is None:
            bill = re.fullmatch(
                r"(?:查(?:询|一下)?|看(?:看|一下)?)?(?:我(?:的)?)?(?P<period>上个月|本月|这个月|最近[0-9]+天)?(?:的)?(?P<category>吃饭|餐饮|交通|购物)?(?:的)?(?:账单|花了多少钱|花了多少|消费)",
                clause,
            )
            if bill:
                period, category = bill["period"], bill["category"]
                args = {"account_ref": account}
                if period in {"上个月", "本月", "这个月"}:
                    first = today.replace(day=1)
                    start = (
                        (first - timedelta(days=1)).replace(day=1) if period == "上个月" else first
                    )
                    end = first if period == "上个月" else today + timedelta(days=1)
                    args.update(
                        start_date=start.isoformat(),
                        end_date=end.isoformat(),
                        timezone="Asia/Shanghai",
                    )
                else:
                    args["days"] = int(re.search(r"[0-9]+", period)[0]) if period else 30
                    if not 1 <= args["days"] <= 366:
                        return question("period", "查询范围须为 1 至 366 天。")
                if category:
                    args["category"] = {
                        "吃饭": "food",
                        "餐饮": "food",
                        "交通": "transport",
                        "购物": "shopping",
                    }[category]
                step = Step(step_ref="bills", action="bills.analyze", arguments=args)
        if step is None:
            order = re.fullmatch(rf"(?P<verb>赎回|申购)\s*(?P<product>.*?)\s*{AMOUNT}", clause)
            if order:
                products = [
                    p for p in context.get("products", []) if p["name"] == order["product"].strip()
                ]
                if not order["product"].strip() and order["verb"] == "赎回":
                    owned = {
                        p["product_ref"]
                        for p in context.get("positions", [])
                        if p["account_ref"] == account and p["value_minor"] > 0
                    }
                    products = [p for p in context.get("products", []) if p["product_ref"] in owned]
                if len(products) != 1:
                    return question("product_order", "请确认产品和申赎金额。")
                redeem = order["verb"] == "赎回"
                step = Step(
                    step_ref="redeem" if redeem else "subscribe",
                    action="product.redeem" if redeem else "product.subscribe",
                    arguments={
                        "account_ref": account,
                        "product_ref": products[0]["product_ref"],
                        "amount_minor": money_minor(order["amount"]),
                        "currency": "CNY",
                        "fee_minor": 0,
                        "minimum_remaining_balance_minor": 0,
                    },
                )
        if step is None:
            cancel = re.fullmatch(
                r"(?:把)?(?:这个月的|本月的)?(?P<name>.*?)订阅(?P<all>都|全部)?取消|取消(?P<prefix>全部|所有)?(?P<other>.*?)订阅",
                clause,
            )
            if cancel:
                name = (cancel["name"] or cancel["other"] or "").strip()
                resources = [
                    r
                    for r in context.get("subscriptions", [])
                    if r["state"] == "ACTIVE"
                    and (
                        not name
                        or (r.get("name") or r.get("data", {}).get("merchant"))
                        in {name, name + "订阅"}
                    )
                ]
                all_requested = cancel["all"] or cancel["prefix"]
                if not resources or (len(resources) > 1 and not all_requested):
                    return question(
                        "subscription_ref",
                        "请指定订阅，或明确取消全部订阅。",
                        [r["resource_ref"] for r in resources],
                    )
                for resource in resources:
                    if len(steps) >= 32:
                        return question("goals", "目标过多，请拆分任务。")
                    steps.append(
                        Step(
                            step_ref=f"cancel-{len(steps) + 1}",
                            action="subscription.cancel",
                            arguments={"subscription_ref": resource["resource_ref"]},
                            depends_on=(steps[-1].step_ref,) if steps else (),
                        )
                    )
                continue
        if step is None and re.fullmatch(r"(?:临时)?(?:锁卡|锁定卡|解锁卡)", clause):
            cards = context.get("cards", [])
            if len(cards) != 1:
                return question("card_ref", "请选择卡片。", [c["resource_ref"] for c in cards])
            step = Step(
                step_ref="card",
                action="card.set_lock",
                arguments={"card_ref": cards[0]["resource_ref"], "locked": "解锁" not in clause},
            )
        if step is None:
            return question("goals", "尚未完整理解本次请求，请确认全部目标；未执行任何片段。")
        identifier = step.step_ref
        if any(s.step_ref == identifier for s in steps):
            identifier += f"-{len(steps) + 1}"
        step = step.model_copy(update={"step_ref": identifier})
        if steps:
            step = step.model_copy(update={"depends_on": (steps[-1].step_ref,)})
        steps.append(step)
    if "到账后" in message:
        redeems = [s for s in steps if s.action == "product.redeem"]
        transfers = [s for s in steps if s.action == "transfer.create"]
        if (
            len(redeems) != 1
            or not transfers
            or steps.index(redeems[0]) >= min(steps.index(s) for s in transfers)
        ):
            return question("goals", "到账条件需要明确的前置赎回和后续转账。")
        steps = [
            s.model_copy(
                update={
                    "depends_on": tuple(dict.fromkeys((*s.depends_on, redeems[0].step_ref))),
                    "funds_available_after": (redeems[0].step_ref,),
                }
            )
            if s.action == "transfer.create"
            else s
            for s in steps
        ]
    return tuple(steps), constraints, []
