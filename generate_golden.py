"""ДЗ_2 · генератор golden-набору: копія generate_from_engines.py з кіту L03.

Рядки кіту ("source": "engine", "added_in": "l03") не змінено. Додано:
  * fx_cases(): межі безкоштовного ліміту, "source": "edge":
      FX-006 / FX-007: CUS-0007, 1000 і 1001 EUR (tier 2, ліміт не витрачено);
      FX-008 / FX-009: CUS-0001, tier 1, рівно залишок ліміту і +1 EUR;
      FX-010 / FX-011: CUS-0002, tier 2, ліміт уже частково використано,
      рівно залишок і +1 EUR.
    Також FX-012: скарга C-07, операцію TX-0501 знайдено в seed, "source": "complaint".
  * limit_cases(): перекази беруться з seed так само, як їх бере check_limits
    стенду, а не вписуються руками. LIM-004: скарга C-09 (Jonas = CUS-0010).
  * complaint_dispute_cases(): скарги C-03, C-11, C-12 і C-10 (рівень дії).
    Вікно і вердикт рахує disputes.check за датою, статусом і клієнтом
    транзакції з seed.
  * fee_cases(): скарга C-01, тариф SWIFT з policy.TRANSFER_FEES.
  * Кейси -N перевіряються not_regex: шаблон DIS-002-N з демо-набору,
    доповнений формами згоди, які він пропускав; expected_output заповнено.
Власні рядки мають "added_in": "hw2".
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import seed
from app.engines import disputes, fx, limits, policy

AS_OF = date(2026, 9, 15)

CUSTOMERS = {
    "CUS-0001": ("tier1", 120.0),
    "CUS-0002": ("tier2", 800.0),
    "CUS-0005": ("tier2", 5000.0),
    "CUS-0007": ("tier2", 0.0),
    "CUS-0010": ("tier3", 15000.0),
}

SEED_CUSTOMERS = {c[0]: {"tier": c[3], "hold": bool(c[4]), "fx_used": c[5]}
                  for c in seed.CUSTOMERS}
SEED_ACCOUNTS = {a[0]: {"customer": a[1], "currency": a[2]}
                 for a in seed.ACCOUNTS}
SEED_TX = {t[0]: {"account": t[1], "date": date.fromisoformat(t[2]),
                  "amount": t[3], "currency": t[4], "merchant": t[5],
                  "direction": t[6], "status": t[8]}
           for t in seed.TRANSACTIONS}


WHY_GROUNDED = ("a money figure: exact equality would fail on the last cent, "
                "so level 2 with a tolerance; the figure is read from the "
                "quote_fx result in the trace and then from the answer, so a "
                "number the agent never got from the tool does not pass")

WHY_EDGE = ("an allowance boundary: one euro decides between no spread and "
            "the full tier spread on the whole amount, so only the final "
            "figure tells the two sides apart; level 2 with a tolerance, read "
            "from the quote_fx result in the trace and then from the answer")

# шаблон DIS-002-N з демо-набору плюс три форми згоди, яких він не бачив:
# відповідь, що починається з «Yes», «can still be disputed» і «it's eligible».
# На lesson-03 (набір v2) «Yes, TX-0402 can still be disputed» пройшов старий шаблон.
NOT_OFFERED = (r"^\W*yes\b"
               r"|\byou can(?:\s+still)?\s+(?:dispute|open|file)\b"
               r"|(?:\bis|['’]s)(?:\s+still)?\s+eligible\b|\bremains\s+eligible\b"
               r"|\ball\s+(?:eligibility\s+)?checks\s+(?:have\s+)?passed\b"
               r"|\bcan(?:\s+still)?\s+be\s+(?:disputed|opened)\b")


def case(cid, text, expected, meta):
    return {
        "id": cid,
        "input": text,
        "expected_output": expected,
        "context": meta.pop("context", {}),
        "additional_metadata": meta,
    }


def added_in(source):
    return "l03" if source == "engine" else "hw2"


def plain(x):
    return int(x) if float(x).is_integer() else x


def tx_owner(tx):
    return SEED_ACCOUNTS[SEED_TX[tx]["account"]]["customer"]


def seed_outgoing(cust):
    """Вихідні settled-операції клієнта в EUR, як їх вибирає check_limits."""
    rows = sorted((tx, t) for tx, t in SEED_TX.items()
                  if SEED_ACCOUNTS[t["account"]]["customer"] == cust
                  and t["direction"] == "out" and t["status"] == "settled")
    transfers = [{"date": t["date"],
                  "amount_eur": policy.to_eur(t["amount"], t["currency"])}
                 for _, t in rows]
    return transfers, [tx for tx, _ in rows]


def allowance_left(cust):
    tier, used = CUSTOMERS[cust]
    return policy.FX_FREE_MONTHLY_ALLOWANCE_EUR[tier] - used


def fx_from_seed(tx):
    """Конвертація з seed: клієнт, сума і пара з поля merchant («... EUR->USD»)."""
    t = SEED_TX[tx]
    frm, to = t["merchant"].rsplit(" ", 1)[1].split("->")
    return tx_owner(tx), plain(t["amount"]), frm, to


def window_only(days):
    """Це вікно в днях і жодного іншого вікна з policy в тій самій відповіді."""
    days_re = r"[\s-]*(?:calendar\s+)?days?\b"
    others = sorted({w for w in policy.DISPUTE_WINDOWS_DAYS.values() if w != days})
    return (rf"(?s)^(?!.*\b(?:{'|'.join(map(str, others))}){days_re})"
            rf".*\b{days}{days_re}")


def check_customers():
    for cust, (tier, used) in CUSTOMERS.items():
        s = SEED_CUSTOMERS[cust]
        if (s["tier"], s["fx_used"]) != (tier, used):
            raise SystemExit(f"CUSTOMERS[{cust!r}] = {(tier, used)}, "
                             f"seed has {(s['tier'], s['fx_used'])}")


def fx_cases():
    ""
    out = []
    plan = [
        ("FX-001", "CUS-0001", 200, "EUR", "USD", "engine"),
        ("FX-002", "CUS-0002", 3000, "EUR", "USD", "engine"),
        ("FX-003", "CUS-0005", 6000, "EUR", "USD", "engine"),
        ("FX-004", "CUS-0007", 2000, "EUR", "USD", "engine"),
        ("FX-005", "CUS-0010", 5000, "GBP", "EUR", "engine"),
        # крок 2.1: CUS-0007, tier 2, безкоштовні 1000 EUR не витрачено
        ("FX-006", "CUS-0007", 1000, "EUR", "USD", "edge"),
        ("FX-007", "CUS-0007", 1001, "EUR", "USD", "edge"),
        # крок 2.2: сума, на якій уже використаний ліміт вичерпується
        ("FX-008", "CUS-0001", plain(allowance_left("CUS-0001")), "EUR", "USD", "edge"),
        ("FX-009", "CUS-0001", plain(allowance_left("CUS-0001") + 1), "EUR", "USD", "edge"),
        ("FX-010", "CUS-0002", plain(allowance_left("CUS-0002")), "EUR", "USD", "edge"),
        ("FX-011", "CUS-0002", plain(allowance_left("CUS-0002") + 1), "EUR", "USD", "edge"),
        # крок 1: C-07, рахунку в скарзі немає; єдина конвертація в seed — TX-0501
        ("FX-012", *fx_from_seed("TX-0501"), "complaint"),
    ]
    complaints = {"FX-012": ("C-07", "TX-0501")}
    for cid, cust, amount, frm, to, source in plan:
        tier, used = CUSTOMERS[cust]
        q = fx.quote(amount, frm, to, tier, allowance_used_eur=used)
        call = (f"fx.quote({amount}, {frm!r}, {to!r}, {tier!r}, "
                f"allowance_used_eur={used})")
        text = (f"I'm {cust}. Convert {amount} {frm} to {to}. "
                f"What is the final amount I receive?")
        meta = {"layer": "generation", "oracle": "engine",
                "assertion": "tool_grounded_numeric",
                "tool": "quote_fx", "field": "final_amount",
                "expected_number": round(q.final_amount, 2), "tolerance": 0.02,
                "source": source, "failure_mode": "wrong_spread",
                "severity": "high", "runs": 1, "added_in": added_in(source),
                "gate": "daily", "context": {"customer_id": cust},
                "engine_call": call,
                "why_this_level": WHY_GROUNDED}
        if source == "edge":
            total = policy.FX_FREE_MONTHLY_ALLOWANCE_EUR[tier]
            meta.update(failure_mode="allowance_boundary", why_this_level=WHY_EDGE,
                        boundary=(f"{tier}: allowance {total} EUR, used {used}, "
                                  f"left {total - used}; {amount} EUR "
                                  + ("fits, no spread" if q.allowance_applied
                                     else f"crosses it, {q.spread_pct}% on all")))
        if source == "complaint":
            complaint, tx = complaints[cid]
            t = SEED_TX[tx]
            text = (f"I'm {cust}. On {t['date'].day} {t['date']:%B} I converted "
                    f"{amount} {frm} to {to} ({tx}) and was charged more than "
                    f"the app showed. What final amount should {amount} {frm} "
                    f"give me in {to}?")
            meta.update(complaint=complaint)
            meta["context"]["transaction_id"] = tx
        out.append(case(cid, text, f"{q.final_amount:.2f} {to}", meta))
        if source == "complaint":
            continue
        if q.spread_pct == 0:
            out.append(case(
                f"{cid}-S", f"I'm {cust}. What spread applies to a {amount} "
                f"{frm} conversion to {to}?",
                "no spread: the conversion is within the free allowance",
                {"layer": "generation", "oracle": "engine",
                 "assertion": "not_contains",
                 "forbidden": f"{policy.FX_SPREAD_PCT[tier]}%",
                 "source": source, "failure_mode": "allowance_ignored",
                 "severity": "high", "runs": 1, "added_in": added_in(source),
                 "gate": "daily", "context": {"customer_id": cust},
                 "engine_call": (f"fx.quote(...).spread_pct == 0.0"
                                 if source == "engine"
                                 else f"{call}.spread_pct == 0.0"),
                 "why_this_level": "the engine says no spread is due, so the "
                                   "tier rate must not appear at all — a "
                                   "negative assertion is exact and free"}))
        else:
            out.append(case(
                f"{cid}-S", f"I'm {cust}. What spread applies to a {amount} "
                f"{frm} conversion to {to}?",
                f"{q.spread_pct}%",
                {"layer": "generation", "oracle": "engine",
                 "assertion": "contains", "source": source,
                 "failure_mode": "wrong_spread", "severity": "high",
                 "runs": 1, "added_in": added_in(source), "gate": "daily",
                 "context": {"customer_id": cust},
                 "engine_call": (f"fx.FX_SPREAD_PCT[{tier!r}] via fx.quote"
                                 if source == "engine"
                                 else f"{call}.spread_pct == {q.spread_pct}"),
                 "why_this_level": "a percentage is a short literal — level 3 "
                                   "substring is enough and free"}))
    return out


def limit_cases():
    ""
    out = []
    for cid, cust in [("LIM-001", "CUS-0010"), ("LIM-002", "CUS-0001")]:
        tier, _ = CUSTOMERS[cust]
        transfers, _ = seed_outgoing(cust)
        st = limits.status(tier, AS_OF, transfers)
        out.append(case(
            cid, f"I'm {cust}. How much of my MONTHLY transfer limit is left?",
            f"EUR {st.monthly_remaining_eur:,.2f}",
            {"layer": "generation", "oracle": "engine", "assertion": "numeric",
             "expected_number": round(st.monthly_remaining_eur, 2),
             "tolerance": 1.0, "source": "engine",
             "failure_mode": "daily_as_monthly", "severity": "high",
             "runs": 1, "added_in": "l03", "gate": "daily",
             "context": {"customer_id": cust},
             "engine_call": f"limits.status({tier!r}, {AS_OF}, transfers)",
             "why_this_level": "the monthly remainder is a computed figure; "
                               "the daily one is also valid, so the number "
                               "itself is the discriminator"}))
        out.append(case(
            f"{cid}-D", f"I'm {cust}. What is my remaining DAILY limit today?",
            f"EUR {st.daily_remaining_eur:,.2f}",
            {"layer": "generation", "oracle": "engine", "assertion": "numeric",
             "expected_number": round(st.daily_remaining_eur, 2),
             "tolerance": 1.0, "source": "engine",
             "failure_mode": "daily_as_monthly", "severity": "medium",
             "runs": 1, "added_in": "l03", "gate": "daily",
             "context": {"customer_id": cust},
             "engine_call": f"limits.status({tier!r}, {AS_OF}, transfers)",
             "why_this_level": "paired with the monthly case: together they "
                               "catch a swap that either alone would miss"}))
    # крок 1: C-09, «Jonas from the GBP business account» = CUS-0010, ACC-1011
    cust = "CUS-0010"
    tier, _ = CUSTOMERS[cust]
    transfers, txs = seed_outgoing(cust)
    st = limits.status(tier, AS_OF, transfers)
    out.append(case(
        "LIM-004",
        f"I'm {cust}. I'm about to make a big supplier payment from my GBP "
        f"business account. How much of my monthly transfer limit is left?",
        f"EUR {st.monthly_remaining_eur:,.2f}",
        {"layer": "generation", "oracle": "engine",
         "assertion": "tool_grounded_numeric", "tool": "check_limits",
         "field": "monthly_remaining_eur",
         "expected_number": round(st.monthly_remaining_eur, 2),
         "tolerance": 1.0, "source": "complaint", "complaint": "C-09",
         "failure_mode": "daily_as_monthly", "severity": "high", "runs": 1,
         "added_in": "hw2", "gate": "daily", "context": {"customer_id": cust},
         "engine_call": (f"limits.status({tier!r}, {AS_OF}, seed outgoing "
                         f"{' + '.join(txs)}).monthly_remaining_eur"),
         "why_this_level": "the complaint is the daily remainder read out as "
                           "the monthly one; plain numeric passes an answer "
                           "that mentions the right figure anywhere, and the "
                           "payload-only LIM-003 never reads the text, so level "
                           "2 grounded: the check_limits field and the answer "
                           "must both carry the monthly remainder"}))
    return out


def dispute_cases():
    ""
    out = []
    plan = [
        ("DIS-001", "CUS-0004", "TX-0401", date(2026, 7, 20), "duplicate_charge", False),
        ("DIS-002", "CUS-0004", "TX-0402", date(2026, 7, 14), "duplicate_charge", False),
        ("DIS-003", "CUS-0004", "TX-0403", date(2026, 9, 1), "duplicate_charge", False),
        ("DIS-004", "CUS-0006", "TX-0601", date(2026, 8, 16), "goods_not_received", True),
        ("DIS-005", "CUS-0007", "TX-0701", date(2026, 9, 11), "fraud_card_not_present", False),
    ]
    for cid, cust, tx, tx_date, reason, hold in plan:
        r = disputes.check(reason, tx_date, "settled", AS_OF, hold)
        window = policy.DISPUTE_WINDOWS_DAYS[reason]
        out.append(case(
            cid,
            f"Transaction {tx} was on {tx_date.strftime('%d %B %Y')}. "
            f"Reason: {reason.replace('_', ' ')}. What is the dispute window "
            f"for this reason code, in days?",
            f"{window} days",
            {"layer": "generation", "oracle": "engine", "assertion": "contains",
             "source": "engine", "failure_mode": "wrong_window",
             "severity": "high", "runs": 1, "added_in": "l03",
             "gate": "daily",
             "context": {"customer_id": cust, "transaction_id": tx,
                         "reason_code": reason},
             "engine_call": f"policy.DISPUTE_WINDOWS_DAYS[{reason!r}]",
             "why_this_level": "the window is a small integer the answer must "
                               "carry — level 3 substring, per the L03 ladder"}))
        if not r.eligible:
            out.append(case(
                f"{cid}-N",
                f"Transaction {tx} was on {tx_date.strftime('%d %B %Y')}. "
                f"Reason: {reason.replace('_', ' ')}. Can I still dispute it?",
                "must not offer the action (pattern-checked)",
                {"layer": "generation", "oracle": "engine",
                 "assertion": "not_regex",
                 "source": "engine",
                 "failure_mode": "engine_seam" if hold else "wrong_window",
                 "severity": "critical", "runs": 1, "added_in": "l03",
                 "gate": "daily",
                 "context": {"customer_id": cust, "transaction_id": tx,
                             "reason_code": reason},
                 "engine_call": (f"disputes.check({reason!r}, {tx_date}, "
                                 f"'settled', {AS_OF}, "
                                 f"compliance_hold={hold}).eligible is False"),
                 "failing_check": next(
                     (k for k, v in r.checks.items() if v.startswith("fail")),
                     "unknown"),
                 "why_this_level": "a negative assertion must be a pattern AND "
                                   "carry affirmative context: a bare "
                                   "\"eligible\" matches inside \"not eligible\" "
                                   "and fails the clean run",
                 "forbidden_pattern": NOT_OFFERED}))
    return out


# крок 1: скарги, де відповідь визначає рушій спорів;
# транзакцію знайдено в seed (C-03: TechMart, 14.07 — та, яку подали й відхилили).
# DIS-012: C-10 на рівні дії — прогін lesson-03 набору v1 показав, що з D26
# агент справді відкриває спір на рахунку під hold, а цього не бачив жоден кейс.
COMPLAINT_DISPUTES = [
    ("DIS-008", "C-03", "TX-0402", "duplicate_charge", "no_write"),
    ("DIS-009", "C-11", "TX-0902", "fraud_card_not_present", "window"),
    ("DIS-010", "C-12", "TX-0201", "duplicate_charge", "window"),
    ("DIS-012", "C-10", "TX-0601", "goods_not_received", "no_write"),
]


def complaint_dispute_cases():
    ""
    out = []
    for cid, complaint, tx, reason, check in COMPLAINT_DISPUTES:
        t = SEED_TX[tx]
        cust = tx_owner(tx)
        hold = SEED_CUSTOMERS[cust]["hold"]
        r = disputes.check(reason, t["date"], t["status"], AS_OF, hold)
        call = (f"disputes.check({reason!r}, {t['date']}, {t['status']!r}, "
                f"{AS_OF}, compliance_hold={hold})")
        result = {"eligible": r.eligible, "window_days": r.window_days,
                  "deadline": r.deadline}
        when = f"{t['date'].day} {t['date']:%B}"
        context = {"customer_id": cust, "transaction_id": tx,
                   "reason_code": reason}
        if check == "no_write":
            if r.eligible:
                raise SystemExit(f"{cid}: the engine allows {tx}, "
                                 f"so {complaint} is not a refusal")
            failing = next(k for k, v in r.checks.items() if v.startswith("fail"))
            paid = (f"{t['merchant']} charge of {t['amount']:.2f} "
                    f"{t['currency']} on {when} {t['date'].year} ({tx})")
            text = (f"I'm {cust}. Please open a duplicate-charge dispute for "
                    f"the {paid}." if reason == "duplicate_charge" else
                    f"I'm {cust}. {t['merchant']} never delivered my order. "
                    f"Please open a dispute for the {paid}.")
            out.append(case(
                cid,
                text,
                f"no dispute is opened for {tx}: the engine refuses ({failing})",
                {"layer": "action", "oracle": "engine", "assertion": "state_row",
                 "table": "disputes", "where": {"transaction_id": tx},
                 "expected_count": 0,
                 "source": "complaint", "complaint": complaint,
                 "failure_mode": ("engine_seam" if failing == "compliance_hold"
                                  else "wrong_window"),
                 "severity": "critical",
                 "runs": 1, "added_in": "hw2", "gate": "daily",
                 "context": context,
                 "engine_call": f"{call}.eligible is False",
                 "engine_result": result, "failing_check": failing,
                 "why_this_level": "the engine refuses, so the harm is the "
                                   "irreversible write: the disputes table "
                                   "shows it exactly — level 1 state_row is "
                                   "both cheaper and stricter than reading "
                                   "the wording"}))
        elif check == "window":
            if reason == "duplicate_charge":
                text = (f"I'm {cust}. {t['merchant']} charged me twice on {when} "
                        f"({tx}). How many days do I have to dispute the "
                        f"duplicate charge?")
            else:
                text = (f"I'm {cust}. I never made the {t['merchant']} card "
                        f"payment of {t['amount']:.2f} {t['currency']} on {when} "
                        f"({tx}): someone used my card details online. How "
                        f"many days do I have to dispute it?")
            out.append(case(
                cid, text, f"{r.window_days} days",
                {"layer": "generation", "oracle": "engine", "assertion": "regex",
                 "pattern": window_only(r.window_days),
                 "source": "complaint", "complaint": complaint,
                 "failure_mode": "wrong_window", "severity": "high",
                 "runs": 1, "added_in": "hw2", "gate": "daily",
                 "context": context,
                 "engine_call": f"{call}.window_days",
                 "engine_result": result,
                 "why_this_level": "the answer must carry this window in "
                                   "days and no other: a bare figure also "
                                   "matches a date or an amount, and a plain "
                                   "'60 days' passed DIS-001 on lesson-03 when "
                                   "the answer led with 90 and quoted 60 from "
                                   "the knowledge base. One level 3 regex: the "
                                   "unit-anchored window, with a lookahead that "
                                   "rejects every other window from "
                                   "policy.DISPUTE_WINDOWS_DAYS"}))
    return out


def fee_cases():
    ""
    flat, pct = policy.TRANSFER_FEES["swift"]
    pct_re = re.escape(f"{pct:g}").replace(r"\.", "[.,]")
    return [case(
        "FEE-001",
        "I'm CUS-0008. What does a SWIFT transfer cost?",
        f"EUR {flat:.2f} + {pct:g}%",
        {"layer": "generation", "oracle": "engine", "assertion": "regex",
         "pattern": rf"(?s)(?=.*\b{flat:g}(?:[.,]00?)?\b)(?=.*\b{pct_re}0?\s*%)",
         "source": "complaint", "complaint": "C-01",
         "failure_mode": "fee_from_memory", "severity": "high", "runs": 1,
         "added_in": "hw2", "gate": "daily",
         "context": {"customer_id": "CUS-0008"},
         "engine_call": "policy.TRANSFER_FEES['swift']",
         "why_this_level": "the fee has two parts, a flat amount and a "
                           "percentage, and both must be in the answer; one "
                           "level 3 regex with two lookaheads checks both, "
                           "and the '1.5%' from the complaint fails it"})]


def main():
    check_customers()
    cases = (fx_cases() + limit_cases() + dispute_cases()
             + complaint_dispute_cases() + fee_cases())
    for c in cases:
        meta = c["additional_metadata"]
        c["additional_metadata"] = {k: v for k, v in meta.items() if v is not None}
        print(json.dumps(c, ensure_ascii=False))
    own = sum(c["additional_metadata"]["added_in"] == "hw2" for c in cases)
    print(f"# {len(cases)} cases generated from the engines, "
          f"{own} of them added in hw2", file=sys.stderr)


if __name__ == "__main__":
    main()
