"""BidStudio pricing: a deterministic commercial model.

Every figure is computed here with decimal arithmetic (and written to the workbook as
formulas that reproduce it); a language model never does the sums.

Terms, kept apart on purpose:

    COST            what the bidder pays (internal)
    MARKUP          (selling - cost) / cost          selling = cost x (1 + markup)
    GROSS_MARGIN    (selling - cost) / selling       selling = cost / (1 - margin)
    CONTINGENCY     a cost provision for risk, priced like the items it protects and
                    built into their selling prices (shown separately only internally)
    SELLING_PRICE   the price before tax, after any discount
    TAX             on the taxable part of the selling price
    TOTAL_BID_PRICE selling price + tax

A line's cost comes from a vendor quotation, the company's rate card, an explicitly
labelled planning estimate, or is PENDING. Pending lines keep the model from being final;
estimates are labelled in every output.
"""
from __future__ import annotations

import copy
import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CATEGORIES = ("labor", "partner", "license", "subscription", "hardware", "hosting", "travel",
              "accommodation", "training", "implementation", "support", "warranty", "other")
SOURCES = ("quotation", "rate_card", "estimate", "pending")
CENT = Decimal("0.01")
ZERO = Decimal("0")


class PricingError(Exception):
    pass


def D(value, what="value"):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise PricingError("%s is not a number: %r" % (what, value))


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def selling_from_cost(cost, rule):
    """Selling price of a cost under a pricing rule {"markup": r} or {"margin": m}."""
    if "markup" in rule and rule["markup"] is not None:
        rate = D(rule["markup"], "markup")
        if rate < -1:
            raise PricingError("a markup below -100% gives a negative price")
        return cost * (1 + rate)
    if "margin" in rule and rule["margin"] is not None:
        rate = D(rule["margin"], "margin")
        if rate >= 1:
            raise PricingError("a gross margin of 100% or more is impossible")
        return cost / (1 - rate)
    raise PricingError("a pricing rule needs a markup or a margin")


def markup_of(cost, selling):
    return None if not cost else (selling - cost) / cost


def margin_of(cost, selling):
    return None if not selling else (selling - cost) / selling


def _rule_for(item, model):
    if item.get("pricing"):
        return item["pricing"]
    rules = model.get("pricing_rules") or {}
    return rules.get(item.get("category")) or rules.get("default") or {"markup": 0}


def _fx(currency, model, shift=ZERO):
    base = model.get("currency", "USD")
    if not currency or currency == base:
        return Decimal(1), None
    rates = model.get("fx") or {}
    entry = rates.get(currency)
    if not entry:
        raise PricingError("no exchange rate from %s to %s" % (currency, base))
    rate = D(entry.get("rate"), "exchange rate %s" % currency)
    return rate * (1 + shift), entry


def compute(model, scenario=None):
    """The full calculation. `scenario` may shift {"fx": 0.05, "labor_effort": 0.1,
    "license": 0.1} (fractions) to test sensitivity. Returns the result dict."""
    scenario = scenario or {}
    base = model.get("currency", "USD")
    lines, issues = [], []
    for raw in model.get("items") or []:
        item = dict(raw)
        source = (item.get("source") or {}).get("type", "pending")
        if item.get("category") not in CATEGORIES:
            issues.append({"level": "error", "item": item.get("id"),
                           "message": "category must be one of %s" % ", ".join(CATEGORIES)})
        if source not in SOURCES:
            issues.append({"level": "error", "item": item.get("id"),
                           "message": "source type must be one of %s" % ", ".join(SOURCES)})
        quantity = D(item.get("quantity", 1), "quantity of %s" % item.get("id"))
        unit_cost = D(item.get("unit_cost"), "unit cost of %s" % item.get("id"))
        if source == "pending" or unit_cost is None:
            lines.append({"id": item.get("id"), "item": item, "pending": True, "source": source,
                          "cost": None, "selling": None})
            issues.append({"level": "pending", "item": item.get("id"),
                           "message": "%s has no confirmed or estimated cost" % item.get("id")})
            continue
        if quantity < 0 or unit_cost < 0:
            issues.append({"level": "error", "item": item.get("id"),
                           "message": "negative quantity or cost"})
        factor = Decimal(1)
        if item.get("category") == "labor" and scenario.get("labor_effort"):
            factor += D(scenario["labor_effort"])
        if item.get("category") in ("license", "subscription") and scenario.get("license"):
            factor += D(scenario["license"])
        fx, fx_entry = _fx(item.get("currency") or base, model,
                           D(scenario.get("fx", 0)) if item.get("currency") not in (None, base)
                           else ZERO)
        cost = quantity * unit_cost * factor * fx
        rule = _rule_for(item, model)
        selling = selling_from_cost(cost, rule)
        lines.append({"id": item.get("id"), "item": item, "pending": False, "source": source,
                      "quantity": quantity * factor if item.get("category") == "labor"
                      else quantity, "unit_cost": unit_cost, "fx": fx,
                      "fx_status": (fx_entry or {}).get("status"), "cost": cost, "rule": rule,
                      "selling_before_contingency": selling, "estimate": source == "estimate"})
    priced = [line for line in lines if not line["pending"]]
    total_cost = sum((line["cost"] for line in priced), ZERO)
    contingency_rate = D(model.get("contingency", 0), "contingency") or ZERO
    contingency_cost = total_cost * contingency_rate
    # contingency is priced like the items it protects: each line carries its share
    for line in priced:
        share = (line["cost"] / total_cost) if total_cost else ZERO
        line["contingency_cost"] = contingency_cost * share
        line["contingency_selling"] = selling_from_cost(line["contingency_cost"], line["rule"])
        raw = line["selling_before_contingency"] + line["contingency_selling"]
        # the unit price is fixed first, so quantity x unit price = line total in the BOQ
        quantity = D(line["item"].get("quantity", 1))
        line["unit_price"] = money(raw / quantity) if quantity else money(raw)
        line["selling"] = money(line["unit_price"] * quantity) if quantity else money(raw)
        line["cost"] = money(line["cost"])
        line["markup"] = markup_of(line["cost"] + money(line["contingency_cost"]), line["selling"])
        line["margin"] = margin_of(line["cost"] + money(line["contingency_cost"]), line["selling"])
    subtotal = sum((line["selling"] for line in priced), ZERO)
    discount_rate = D(model.get("discount", 0), "discount") or ZERO
    discount = money(subtotal * discount_rate)
    selling_price = subtotal - discount
    taxable = sum((line["selling"] for line in priced if line["item"].get("taxable", True)), ZERO)
    taxable_after_discount = taxable - money(taxable * discount_rate)
    tax_rate = D(model.get("tax_rate", 0), "tax rate") or ZERO
    tax = money(taxable_after_discount * tax_rate)
    cost_with_contingency = money(total_cost) + money(contingency_cost)
    totals = {
        "currency": base,
        "COST": money(total_cost),
        "CONTINGENCY": money(contingency_cost),
        "COST_WITH_CONTINGENCY": cost_with_contingency,
        "SUBTOTAL": money(subtotal),
        "DISCOUNT": discount,
        "SELLING_PRICE": money(selling_price),
        "MARKUP": markup_of(cost_with_contingency, selling_price),
        "GROSS_MARGIN": margin_of(cost_with_contingency, selling_price),
        "TAX": tax,
        "TOTAL_BID_PRICE": money(selling_price + tax),
    }
    pending = [line["id"] for line in lines if line["pending"]]
    estimates = [line["id"] for line in priced if line["estimate"]]
    assumed_fx = sorted({line["item"].get("currency") for line in priced
                         if line.get("fx_status") not in (None, "CONFIRMED")
                         and line["item"].get("currency") not in (None, base)})
    for line in priced:
        if line["selling"] < line["cost"]:
            issues.append({"level": "warning", "item": line["id"],
                           "message": "sold below cost"})
    floor = model.get("minimum_margin")
    if floor is not None and totals["GROSS_MARGIN"] is not None \
            and totals["GROSS_MARGIN"] < D(floor):
        issues.append({"level": "warning", "item": "total",
                       "message": "gross margin %.1f%% is below the minimum %.1f%%"
                       % (totals["GROSS_MARGIN"] * 100, D(floor) * 100)})
    status = ("not started" if not lines else
              "incomplete" if pending or any(i["level"] == "error" for i in issues)
              else "estimated" if estimates or assumed_fx else "complete")
    return {"lines": lines, "totals": totals, "issues": issues, "pending": pending,
            "estimates": estimates, "assumed_fx": assumed_fx, "status": status,
            "scenario": scenario}


def sensitivity(model, shifts=None):
    """Totals under shifted exchange rates, labour effort and licence costs."""
    shifts = shifts or [("baseline", {}), ("fx +5%", {"fx": "0.05"}), ("fx -5%", {"fx": "-0.05"}),
                        ("effort +20%", {"labor_effort": "0.20"}),
                        ("effort -10%", {"labor_effort": "-0.10"}),
                        ("licences +10%", {"license": "0.10"})]
    out = []
    for label, shift in shifts:
        result = compute(model, shift)
        out.append({"scenario": label, "shift": shift, "COST": result["totals"]["COST"],
                    "SELLING_PRICE": result["totals"]["SELLING_PRICE"],
                    "GROSS_MARGIN": result["totals"]["GROSS_MARGIN"],
                    "TOTAL_BID_PRICE": result["totals"]["TOTAL_BID_PRICE"]})
    return out


def digest(model):
    """A stable hash of the pricing inputs: an approval is for exactly this model."""
    return hashlib.sha256(json.dumps(model, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def labor_items(estimate, rate_card, currency):
    """Pricing lines from an effort estimate (person-days by role, baseline scenario) and
    a rate card {"roles": {role: {"daily_cost": .., "currency": ..}}}."""
    roles = (rate_card or {}).get("roles") or {}
    items = []
    for role, days in sorted((estimate.get("by_role") or {}).items()):
        entry = roles.get(role)
        items.append({
            "id": "LAB-%s" % role.upper().replace(" ", "-")[:20], "category": "labor",
            "description": "%s services" % role, "quantity": str(days), "unit": "person-day",
            "unit_cost": entry.get("daily_cost") if entry else None,
            "currency": (entry or {}).get("currency") or currency,
            "source": {"type": "rate_card", "ref": "rate card"} if entry
            else {"type": "pending", "ref": "no rate for %s" % role},
            "boq": True, "taxable": True})
    return items


def customer_lines(result):
    """What a customer may see: description, quantity, unit, unit price, line total. No
    cost, markup, margin, contingency, supplier or source."""
    out = []
    for line in result["lines"]:
        if line["pending"]:
            out.append({"id": line["id"], "description": line["item"].get("customer_description")
                        or line["item"].get("description"), "quantity": line["item"].get("quantity"),
                        "unit": line["item"].get("unit"), "unit_price": None, "total": None})
            continue
        item = line["item"]
        if item.get("boq") is False:
            continue
        out.append({"id": line["id"],
                    "description": item.get("customer_description") or item.get("description"),
                    "quantity": item.get("quantity", 1), "unit": item.get("unit") or "lot",
                    "unit_price": line["unit_price"], "total": line["selling"],
                    "category": item.get("category")})
    return out


def internal_values(result):
    """Figures that must never appear in a customer-facing document: costs, contingency,
    unit costs (as formatted strings, for leak detection)."""
    values = set()
    for line in result["lines"]:
        if line["pending"]:
            continue
        for key in ("cost", "unit_cost"):
            value = line.get(key)
            if value is not None and value != line.get("selling"):
                values.add(fmt(money(value)))
        if line.get("contingency_cost"):
            values.add(fmt(money(line["contingency_cost"])))
    for key in ("COST", "CONTINGENCY", "COST_WITH_CONTINGENCY"):
        values.add(fmt(result["totals"][key]))
    return {value for value in values if value not in ("0.00",)}


def fmt(value):
    return "{:,.2f}".format(value) if value is not None else "pending"


def pct(value):
    return "-" if value is None else "%.1f%%" % (value * 100)


def jsonable(result):
    out = copy.deepcopy(result)

    def convert(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        return value
    return convert(out)
