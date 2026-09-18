import json
import math
import os
import yaml

# ---------------------------------------------------------------------------
# DEFAULTS — every assumption the model uses lives here, so it's visible and
# editable via home_model_config.yaml without touching this file.
# ---------------------------------------------------------------------------

DEFAULTS = {
    # --- The deal ---
    "offer_price": 1000000.0,
    "personal_contribution": 200000.0,
    "loan_amount": 800000.0,
    "interest_rate": 0.0429,
    "loan_term_years": 30,
    "mortgage_type": "annuity",   # "annuity" (flat payment) or "linear" (fixed principal, falling payment)

    # --- One-time buying costs ---
    "makelaar_commission_pct": 0.0097,
    "taxatierapport": 850,
    "structural_survey": 1000,
    "notary_transfer_deed": 1500,
    "notary_mortgage_deed": 650,
    "transfer_tax_pct": 0.02,
    "mortgage_advisor_fee": 8000,
    "furnishing": 25000,

    # --- Ongoing ownership costs ---
    "vve_monthly": 260.0,
    "ground_lease_annual": 0,
    "ozb_rate": 0.000527,           # % of home value/yr (WOZ-based, NOT loan size)
    "home_insurance_annual": 600,
    "maintenance_pct": 0.004,       # % of home value/yr

    # --- Income ---
    "net_income_monthly": 12000.00,
    "net_income_monthly_after_ruling": 10000.00,

    # --- Rent counterfactual ---
    "current_rent_monthly": 2500,
    "rent_flat_years": 2,
    "rent_after_flat_monthly": 3500,
    "rent_growth_pct": 0.02,   # simple assumption; NL free-sector cap is currently ~4.4%/yr (CPI+1pp, formula-based, through 2029) — see project notes

    # --- Dutch mortgage tax relief (2026) ---
    "mortgage_interest_deduction_rate": 0.3756,
    "eigenwoningforfait_rate": 0.0035,   # % of WOZ/yr, for WOZ up to ewf_tier_threshold
    "ewf_tier_threshold": 1330000,       # above this WOZ, a different formula applies
    "ewf_tier_base": 4655,               # flat euro amount at the threshold
    "ewf_tier_rate": 0.0235,             # rate applied to WOZ above the threshold
    # WOZ (municipal assessed value) used for EWF and OZB — a separate, lagging
    # assessment from market value, NOT the same as offer_price or the
    # appreciation-scenario home value. Defaults to offer_price if unset.
    "woz_value": None,

    # --- Exit costs ---
    "sale_commission_pct": 0.015,
    "mortgage_discharge_fee": 500,

    # --- Box 3 wealth tax (2026) — down-payment relief only; a stock-arbitrage
    # move that happens regardless of the buy/rent decision is NOT modeled here
    # since it nets out equally on both sides of the comparison ---
    "box3_deemed_return": 0.0604,
    "box3_tax_rate": 0.36,
    "box3_savings_deemed_return": 0.0144,
    "down_payment_cash_source": "idle",   # "idle" (savings-bucket cash) or "invested" (equities)
    "idle_cash_return": 0.015,
    "idle_cash_ceiling": 200000.0,        # how much idle cash is actually available before you'd have to sell equities
    "capital_gains_tax_rate": 0.238,      # est. US federal LTCG (20%) + NIIT (3.8%) on any equity sale beyond the ceiling
    "investment_return_pct": 0.05,
    "box3_on_portfolio": True,
    "reinvest_monthly_delta": True,

    # --- ABN AMRO Budget mortgage, energy label A, 10-year fixed, client rate
    # (Sept 2026). Discrete tariefklasse bands — the rate that applies to a
    # given LTV is a step function, NOT interpolated between tiers. NHG
    # excluded: its price cap (~€450k) is far below this purchase price, so
    # it's never actually available here despite being on the published table. ---
    "rate_tiers": [
        {"max_ltv": 0.65, "rate": 0.0427},
        {"max_ltv": 0.85, "rate": 0.0429},
        {"max_ltv": 0.90, "rate": 0.0431},
        {"max_ltv": 1.00, "rate": 0.0433},
    ],

    # --- Horizons to evaluate ---
    "hold_years_grid": [3, 5, 8],

    # --- Appreciation scenarios to stress-test. Each is either a constant
    # annual rate, or an explicit year-by-year path (for a crash-and-recovery
    # shape) that holds at `path_tail_rate` once the listed years run out. ---
    "appreciation_scenarios": [
        {"name": "-5%/yr sustained",              "type": "constant", "rate": -0.05},
        {"name": "2008-analog crash",              "type": "path",
         "path": [-0.09, -0.05, -0.04, -0.03, -0.03, 0.02, 0.05, 0.08, 0.09, 0.03,
                  0.05, 0.05, 0.05, 0.05, 0.05],
         "path_tail_rate": 0.03},
        {"name": "-2%/yr sustained",               "type": "constant", "rate": -0.02},
        {"name": "0%/yr (calibrated base case)",   "type": "constant", "rate": 0.0},
        {"name": "+2%/yr (conservative optimism)", "type": "constant", "rate": 0.02},
    ],

    # --- Affordability rating bands (share of net income spent on housing).
    # All three cutoffs come from published Dutch/CBS guidance: comfortable
    # target 28-30% of net income, recommended maximum 33%. ---
    "affordability_ideal_max": 0.28,
    "affordability_acceptable_max": 0.30,
    "affordability_stretched_max": 0.33,

    # --- Stress test: rate shock applied at year-10 fixed-rate renewal ---
    "stress_rate_bump": 0.02,
}

# ---------------------------------------------------------------------------
# PROMPT HELPERS
# ---------------------------------------------------------------------------

def prompt_float(label, default, fmt=".2f"):
    val = input(f"  {label} [{default:{fmt}}]: ").strip()
    return float(val) if val else default

def prompt_int(label, default):
    val = input(f"  {label} [{default}]: ").strip()
    return int(val) if val else default

def prompt_pct(label, default):
    display = default * 100
    val = input(f"  {label} [{display:.4f}%] (enter as %, e.g. 3.85): ").strip()
    return float(val) / 100 if val else default

def prompt_str(label, default, valid=None):
    hint = f" (options: {', '.join(valid)})" if valid else ""
    while True:
        val = input(f"  {label}{hint} [{default}]: ").strip()
        result = val if val else default
        if valid is None or result in valid:
            return result
        print(f"    Invalid — must be one of: {', '.join(valid)}")

# ---------------------------------------------------------------------------
# CONFIG SAVE / LOAD
# ---------------------------------------------------------------------------

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "home_model_config.yaml")
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_output.json")

def save_config(scenario_params):
    data = []
    for sp in scenario_params:
        entry = {"name": sp.get("_name", "Scenario")}
        entry.update({k: v for k, v in sp.items() if k != "_name"})
        data.append(entry)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump({"scenarios": data}, f, default_flow_style=False, allow_unicode=True)
    print(f"  Config saved → {CONFIG_PATH}")


def load_config():
    """Return list of scenario param dicts from YAML, or None if file absent/invalid."""
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        scenarios = data.get("scenarios", [])
        result = []
        for entry in scenarios:
            sp = DEFAULTS.copy()          # fill any keys the file predates
            sp.update({k: v for k, v in entry.items() if k != "name"})
            sp["_name"] = entry.get("name", "Scenario")
            result.append(sp)
        return result if result else None
    except Exception as e:
        print(f"  Warning: could not load config ({e})")
        return None

# ---------------------------------------------------------------------------
# INPUT GATHERING (interactive fallback — most runs should just edit the YAML)
# ---------------------------------------------------------------------------

def gather_inputs(base=None):
    d = (base or DEFAULTS).copy()

    print("\n--- Home Purchase ---")
    d["offer_price"]           = prompt_float("Offer price (EUR)", d["offer_price"], ".0f")
    d["personal_contribution"] = prompt_float("Personal contribution (EUR)", d["personal_contribution"], ".0f")
    d["loan_amount"]           = d["offer_price"] - d["personal_contribution"]
    print(f"  Loan amount (derived):  {d['loan_amount']:,.0f}")
    d["interest_rate"]         = prompt_pct("Annual interest rate", d["interest_rate"])
    d["loan_term_years"]       = prompt_int("Loan term (years)", d["loan_term_years"])

    print("\n--- One-Time Costs ---")
    d["furnishing"]              = prompt_float("Furnishing budget (EUR)", d["furnishing"], ".0f")

    print("\n--- Ongoing Monthly / Annual Costs ---")
    d["vve_monthly"]           = prompt_float("VVE monthly (EUR)", d["vve_monthly"], ".0f")
    d["ground_lease_annual"]   = prompt_float("Ground lease annual (EUR, 0=freehold)", d["ground_lease_annual"], ".0f")
    d["home_insurance_annual"] = prompt_float("Home insurance annual (EUR)", d["home_insurance_annual"], ".0f")

    print("\n--- Income ---")
    d["net_income_monthly"]   = prompt_float("Combined net monthly (EUR)", d["net_income_monthly"], ".2f")

    print("\n--- Rent Baseline ---")
    d["current_rent_monthly"] = prompt_float("Current rent monthly (EUR)", d["current_rent_monthly"], ".0f")

    print("\n--- Scenario Assumptions ---")
    d["maintenance_pct"]       = prompt_pct("Annual maintenance (% of home value)", d["maintenance_pct"])

    return d


def gather_scenarios():
    print("\n" + "=" * 60)
    print("  Dutch Home Purchase Model — Input Configuration")
    print("  Press ENTER to accept the default value shown in [ ]")
    print("=" * 60)

    saved = load_config()
    if saved:
        names = ", ".join(s.get("_name", "?") for s in saved)
        use_saved = input(f"\n  Saved config found ({names}). Load it? [y/n]: ").strip().lower()
        if use_saved == "y":
            print(f"  Loaded {len(saved)} scenario(s) from config.")
            return saved

    scenarios = []
    params = gather_inputs()
    name = prompt_str("Scenario name", "Base")
    params["_name"] = name
    scenarios.append(params)

    while len(scenarios) < 4:
        again = input("\n  Add another scenario? [y/n]: ").strip().lower()
        if again != "y":
            break
        print("  (Press ENTER to keep the previous scenario's value)")
        variant = gather_inputs(base=scenarios[-1])
        name = prompt_str("Scenario name", f"Scenario {len(scenarios) + 1}")
        variant["_name"] = name
        scenarios.append(variant)

    return scenarios

# ---------------------------------------------------------------------------
# CALC HELPERS
# ---------------------------------------------------------------------------

def _woz(p):
    """WOZ value to use for EWF/OZB. Defaults to offer_price if not set
    separately — see woz_value in DEFAULTS."""
    return p.get("woz_value") or p["offer_price"]


def _ewf_annual(p, woz):
    """Eigenwoningforfait, annual. Flat rate below the threshold; a fixed
    euro base plus a lower marginal rate on the excess above it."""
    threshold = p["ewf_tier_threshold"]
    if woz <= threshold:
        return woz * p["eigenwoningforfait_rate"]
    return p["ewf_tier_base"] + p["ewf_tier_rate"] * (woz - threshold)


def annuity_payment(loan, rate_annual, years):
    r = rate_annual / 12
    n = years * 12
    if r == 0:
        return loan / n
    return loan * r / (1 - (1 + r) ** -n)


def calc_one_time_costs(p):
    items = {
        "Makelaar commission":    p["offer_price"] * p["makelaar_commission_pct"],
        "Taxatierapport":         p["taxatierapport"],
        "Structural survey":      p["structural_survey"],
        "Notary – transfer deed": p["notary_transfer_deed"],
        "Notary – mortgage deed": p["notary_mortgage_deed"],
        "Transfer tax":           p["offer_price"] * p["transfer_tax_pct"],
        "Mortgage advisor fee":   p["mortgage_advisor_fee"],
        "Furnishing":             p["furnishing"],
    }
    items["TOTAL"] = sum(items.values())
    return items


def calc_amortization(p):
    """360-month amortization schedule. EWF (eigenwoningforfait) and the
    resulting mortgage-interest tax benefit are based on home VALUE
    (offer_price), never loan size — both are WOZ-based Dutch taxes."""
    n = p["loan_term_years"] * 12
    r = p["interest_rate"] / 12
    L = p["loan_amount"]
    mid_rate = p["mortgage_interest_deduction_rate"]
    ewf_monthly = _ewf_annual(p, _woz(p)) / 12

    monthly_payment = annuity_payment(L, p["interest_rate"], p["loan_term_years"])

    rows = []
    balance = L
    for m in range(1, n + 1):
        interest = balance * r
        principal = monthly_payment - interest
        balance -= principal
        if abs(balance) < 0.01:
            balance = 0.0
        net_deductible = max(0.0, interest - ewf_monthly)
        tax_benefit = net_deductible * mid_rate
        rows.append({
            "month":             m,
            "year":              math.ceil(m / 12),
            "payment":           monthly_payment,
            "interest":          interest,
            "principal":         principal,
            "balance":           balance,
            "tax_benefit":       tax_benefit,
            "net_mortgage_cost": monthly_payment - tax_benefit,
        })
    return rows, monthly_payment


def calc_amortization_linear(p):
    """360-month LINEAR amortization: a fixed principal payment every month
    (loan / term_months), plus interest on the shrinking balance — so the
    total payment is highest in month 1 and falls every month after.
    Same row schema as calc_amortization() so every downstream function
    (calc_scenario_pnl, calc_affordability) works unchanged either way."""
    n = p["loan_term_years"] * 12
    r = p["interest_rate"] / 12
    L = p["loan_amount"]
    mid_rate = p["mortgage_interest_deduction_rate"]
    ewf_monthly = _ewf_annual(p, _woz(p)) / 12
    fixed_principal = L / n

    rows = []
    balance = L
    for m in range(1, n + 1):
        interest = balance * r
        principal = fixed_principal
        payment = principal + interest
        balance -= principal
        if abs(balance) < 0.01:
            balance = 0.0
        net_deductible = max(0.0, interest - ewf_monthly)
        tax_benefit = net_deductible * mid_rate
        rows.append({
            "month":             m,
            "year":              math.ceil(m / 12),
            "payment":           payment,
            "interest":          interest,
            "principal":         principal,
            "balance":           balance,
            "tax_benefit":       tax_benefit,
            "net_mortgage_cost": payment - tax_benefit,
        })
    return rows, rows[0]["payment"]


def calc_amortization_for(p):
    """Dispatch to the right amortization engine based on p['mortgage_type']
    ("annuity", the default, or "linear")."""
    if p.get("mortgage_type", "annuity") == "linear":
        return calc_amortization_linear(p)
    return calc_amortization(p)


def _monthly_appreciation_factor(scenario, year):
    """Given an appreciation-scenario dict and a 1-indexed year, return the
    per-month growth factor to apply throughout that year."""
    if scenario["type"] == "constant":
        annual = scenario["rate"]
    else:
        path = scenario["path"]
        idx = year - 1
        annual = path[idx] if idx < len(path) else scenario.get("path_tail_rate", 0.0)
    return (1 + annual) ** (1 / 12)


def _rent_for_month(p, month):
    """Flat current_rent_monthly for rent_flat_years, then rent_after_flat_monthly
    growing at rent_growth_pct/yr from there (simple annual compounding)."""
    year = math.ceil(month / 12)
    flat = p["rent_flat_years"]
    if year <= flat:
        return p["current_rent_monthly"]
    growth = p.get("rent_growth_pct", 0.0)
    return p["rent_after_flat_monthly"] * (1 + growth) ** (year - flat - 1)


def calc_scenario_pnl(p, amort_rows, one_time_total, appreciation_scenario, box3_monthly_saving):
    """The core engine: month-by-month true cost of owning (interest net of
    tax benefit and Box 3 relief, plus fixed ownership costs — principal
    excluded, since it's recoverable equity, not a true cost) vs. cumulative
    rent, plus the home-value/equity path under the given appreciation
    scenario. Returns per-month rows and the breakeven month (if any).

    This single function replaces what used to be three separate, partially
    duplicative buy-vs-rent engines (see CLAUDE.md history) — one
    methodology, validated once, used everywhere.
    """
    vve = p["vve_monthly"]
    ground = p["ground_lease_annual"] / 12
    ozb = p["ozb_rate"] * _woz(p) / 12
    insurance = p["home_insurance_annual"] / 12
    maintenance = p["maintenance_pct"] * p["offer_price"] / 12

    home_value = p["offer_price"]
    cum_true_cost = one_time_total
    cum_rent = 0.0
    breakeven_month = None
    rows = []

    for a in amort_rows:
        home_value *= _monthly_appreciation_factor(appreciation_scenario, a["year"])
        rent = _rent_for_month(p, a["month"])
        cum_rent += rent

        true_cost = (a["interest"] - a["tax_benefit"]) + vve + ground + ozb + insurance + maintenance - box3_monthly_saving
        cum_true_cost += true_cost

        appreciation_gain = home_value - p["offer_price"]
        appr_adj_true_cost = cum_true_cost - appreciation_gain

        sale_commission = home_value * p["sale_commission_pct"]
        selling_costs = sale_commission + p["mortgage_discharge_fee"]
        net_gain_if_sold = -(appr_adj_true_cost + selling_costs)   # positive = ahead, all-in, vs. never having bought
        vs_rent_if_sold = net_gain_if_sold + cum_rent               # positive = ahead of renting, all-in

        # Breakeven is defined on the SAME basis as vs_rent_if_sold (i.e.
        # including selling costs) — it's "the year you'd actually come out
        # ahead of renting if you sold," not a different, softer definition.
        if breakeven_month is None and vs_rent_if_sold >= 0:
            breakeven_month = a["month"]

        rows.append({
            "month": a["month"],
            "year": a["year"],
            "home_value": home_value,
            "balance": a["balance"],
            "equity_raw": home_value - a["balance"],
            "equity_net_of_selling_costs": home_value - selling_costs - a["balance"],
            "cum_true_cost": cum_true_cost,
            "cum_rent": cum_rent,
            "net_gain_if_sold": net_gain_if_sold,
            "vs_rent_if_sold": vs_rent_if_sold,
        })

    return rows, breakeven_month


def calc_affordability(p, amort_rows, box3_monthly_saving):
    """LTV, stress-tested monthly cost, and the CBS-guideline-based
    affordability rating (see DEFAULTS for the band cutoffs and source).

    Uses the NET monthly cost of ownership (gross payment, minus the
    year-1 average mortgage-interest tax benefit, minus the Box 3 relief)
    — the same "net monthly cost of ownership" figure used everywhere else
    in the model and in the published report, so this rating is never
    computed on a different basis than the numbers a reader sees elsewhere.
    """
    # month-1 payment, read from the actual schedule so this works for either
    # mortgage_type ("annuity" = flat for life; "linear" = highest in month 1)
    gross_payment = amort_rows[0]["payment"]
    year1 = [a for a in amort_rows if a["year"] == 1]
    avg_tax_benefit_y1 = sum(a["tax_benefit"] for a in year1) / len(year1)

    maintenance_monthly = p["maintenance_pct"] * p["offer_price"] / 12
    vve = p["vve_monthly"]
    ground = p["ground_lease_annual"] / 12
    ozb = p["ozb_rate"] * _woz(p) / 12
    insurance = p["home_insurance_annual"] / 12
    fixed_costs = vve + ground + ozb + insurance + maintenance_monthly

    net_mortgage_cost = gross_payment - avg_tax_benefit_y1 - box3_monthly_saving
    total_own = net_mortgage_cost + fixed_costs
    total_own_pct_net = total_own / p["net_income_monthly"]
    ltv = p["loan_amount"] / p["offer_price"]

    # Stress test: re-amortize month 1 at rate+bump, same mortgage_type
    stress_p = dict(p, interest_rate=p["interest_rate"] + p["stress_rate_bump"])
    stress_rows, stress_payment = calc_amortization_for(stress_p)
    # Stress test keeps the same tax benefit/Box 3 relief — only the rate shocks
    stress_net_mortgage_cost = stress_payment - avg_tax_benefit_y1 - box3_monthly_saving
    stress_total_own = stress_net_mortgage_cost + fixed_costs
    stress_pct_net = stress_total_own / p["net_income_monthly"]

    def rate(pct_net):
        if pct_net <= p["affordability_ideal_max"]:
            return "Ideal", "#2e7d32"
        elif pct_net <= p["affordability_acceptable_max"]:
            return "Acceptable", "#e65100"
        elif pct_net <= p["affordability_stretched_max"]:
            return "Stretched", "#c62828"
        else:
            return "Exceeds", "#b71c1c"

    verdict, verdict_color = rate(total_own_pct_net)
    verdict_after_ruling, verdict_after_ruling_color = (None, None)
    if p.get("net_income_monthly_after_ruling"):
        pct_after = total_own / p["net_income_monthly_after_ruling"]
        verdict_after_ruling, verdict_after_ruling_color = rate(pct_after)
    else:
        pct_after = None

    return {
        "ltv": ltv,
        "monthly_payment_gross": gross_payment,
        "maintenance_monthly": maintenance_monthly,
        "total_own_gross": total_own,
        "total_own_pct_net": total_own_pct_net,
        "total_own_pct_net_after_ruling": pct_after,
        "stress_payment": stress_payment,
        "stress_total_own": stress_total_own,
        "stress_pct_net": stress_pct_net,
        "verdict": verdict,
        "verdict_color": verdict_color,
        "verdict_after_ruling": verdict_after_ruling,
        "verdict_after_ruling_color": verdict_after_ruling_color,
    }


def _rate_for_ltv(p, ltv):
    """Look up the ABN AMRO tariefklasse rate for a given LTV — a step
    function over p['rate_tiers'], not interpolated."""
    for tier in sorted(p["rate_tiers"], key=lambda t: t["max_ltv"]):
        if ltv <= tier["max_ltv"] + 1e-9:
            return tier["rate"]
    return p["rate_tiers"][-1]["rate"]


def calc_down_payment_scenarios(p):
    """Q: does putting more cash in as a down payment beat keeping it, and
    does it meaningfully cut the Box 3 bill?

    Benefit of an extra €X down:
      + after-tax mortgage interest avoided over the loan term
      + Box 3 no longer owed on that €X (source-dependent — see toggle)
    Cost:
      - investment/yield return foregone, net of Box 3 on the alternative
    """
    step = 5000
    contribs = list(range(0, 700000, step))
    base_contrib = p["personal_contribution"]
    n = p["loan_term_years"]
    mid_rate = p["mortgage_interest_deduction_rate"]

    def rate_at(contrib):
        loan = max(0, p["offer_price"] - contrib)
        ltv = loan / p["offer_price"] if p["offer_price"] else 0
        return _rate_for_ltv(p, ltv)

    base_rate = rate_at(base_contrib)
    base_loan = max(0, p["offer_price"] - base_contrib)
    base_pmt = annuity_payment(base_loan, base_rate, n) if base_loan > 0 else 0
    base_total_interest = base_pmt * n * 12 - base_loan

    b3_tax = p["box3_tax_rate"]
    invested_b3_rate = p["box3_deemed_return"] * b3_tax
    idle_b3_rate = p["box3_savings_deemed_return"] * b3_tax
    idle_cash_return = p.get("idle_cash_return", 0.015)
    b3_on = p.get("box3_on_portfolio", True)
    idle_ceiling = p.get("idle_cash_ceiling", base_contrib)
    cgt_rate = p.get("capital_gains_tax_rate", 0.0)

    idle_net_return = max(0.0, idle_cash_return - (idle_b3_rate if b3_on else 0.0))
    invested_net_return = max(0.0, p["investment_return_pct"] - (invested_b3_rate if b3_on else 0.0))

    rows = []
    best_net_benefit = None
    best_idx = None
    for i, contrib in enumerate(contribs):
        loan = max(0, p["offer_price"] - contrib)
        rate = rate_at(contrib)
        pmt = annuity_payment(loan, rate, n) if loan > 0 else 0
        total_interest = pmt * n * 12 - loan if loan > 0 else 0
        interest_saved = base_total_interest - total_interest
        interest_saved_after_tax = interest_saved * (1 - mid_rate)
        extra_cash = contrib - base_contrib

        # Cash sourcing is two-tiered: up to idle_cash_ceiling it's idle
        # savings (no tax event to access it); anything beyond that has to
        # come from selling equities — a real, one-time capital-gains tax
        # hit, plus a higher (equities) opportunity-cost rate going forward.
        idle_available_beyond_base = max(0.0, idle_ceiling - base_contrib)
        from_idle = max(0.0, min(extra_cash, idle_available_beyond_base)) if extra_cash > 0 else extra_cash
        from_equities = extra_cash - from_idle if extra_cash > 0 else 0.0

        box3_saved_term = (from_idle * ((1 + idle_b3_rate) ** n - 1) if from_idle > 0 else
                           from_idle * ((1 + idle_b3_rate) ** n - 1))  # symmetric for negative from_idle (freeing cash)
        box3_saved_term += from_equities * ((1 + invested_b3_rate) ** n - 1)

        opp_cost = (from_idle * ((1 + idle_net_return) ** n - 1)
                    + from_equities * ((1 + invested_net_return) ** n - 1))

        capital_gains_tax = from_equities * cgt_rate   # one-time, paid now, not compounded

        net_benefit = interest_saved_after_tax + box3_saved_term - opp_cost - capital_gains_tax
        if extra_cash >= 0 and (best_net_benefit is None or net_benefit > best_net_benefit):
            best_net_benefit = net_benefit
            best_idx = i
        rows.append({
            "contribution": contrib,
            "loan": loan,
            "ltv": loan / p["offer_price"] if p["offer_price"] else 0,
            "rate": rate,
            "monthly_payment": pmt,
            "monthly_savings": base_pmt - pmt,
            "interest_saved_after_tax": interest_saved_after_tax,
            "box3_saved_term": box3_saved_term,
            "opp_cost": opp_cost,
            "capital_gains_tax": capital_gains_tax,
            "from_equities": from_equities,
            "net_benefit": net_benefit,
            "is_base": contrib == base_contrib,
            "is_optimal": False,
        })
    if best_idx is not None:
        rows[best_idx]["is_optimal"] = True
    return {
        "rows": rows,
        "idle_cash_ceiling": idle_ceiling,
        "idle_net_return": idle_net_return,
        "invested_net_return": invested_net_return,
        "capital_gains_tax_rate": cgt_rate,
    }

# ---------------------------------------------------------------------------
# MAIN — computes everything and writes model_output.json.
# No HTML is generated here; the report is built separately as a published
# artifact, reading this JSON. Run this after editing home_model_config.yaml.
# ---------------------------------------------------------------------------

def main():
    scenario_params = gather_scenarios()
    save_config(scenario_params)
    print("\n  Calculating...")

    output_scenarios = []
    for sp in scenario_params:
        one_time = calc_one_time_costs(sp)
        amort_rows, monthly_payment = calc_amortization_for(sp)
        dp_scenarios = calc_down_payment_scenarios(sp)

        # Box 3 down-payment relief: only the cash actually removed from Box 3
        # by becoming home equity. Uses the savings-bucket deemed return if
        # the down payment came from idle cash (the common case), or the
        # higher equities deemed return if it came from investments.
        b3_tax = sp["box3_tax_rate"]
        if sp.get("down_payment_cash_source", "idle") == "idle":
            b3_rate = sp["box3_savings_deemed_return"] * b3_tax
        else:
            b3_rate = sp["box3_deemed_return"] * b3_tax
        box3_annual_saving = sp["personal_contribution"] * b3_rate
        box3_monthly_saving = box3_annual_saving / 12

        affordability = calc_affordability(sp, amort_rows, box3_monthly_saving)

        hold_years_grid = sp.get("hold_years_grid", [3, 5, 8])
        hold_months = sorted(set(int(round(h * 12)) for h in hold_years_grid))

        appreciation_results = []
        for scen in sp["appreciation_scenarios"]:
            rows, breakeven_month = calc_scenario_pnl(sp, amort_rows, one_time["TOTAL"], scen, box3_monthly_saving)
            snapshots = [r for r in rows if r["month"] in hold_months]
            appreciation_results.append({
                "scenario_name": scen["name"],
                "breakeven_month": breakeven_month,
                "breakeven_year": round(breakeven_month / 12, 1) if breakeven_month else None,
                "snapshots": snapshots,
            })

        output_scenarios.append({
            "name": sp["_name"],
            "params": sp,
            "one_time_costs": one_time,
            "monthly_payment_gross": monthly_payment,
            "box3_annual_saving": box3_annual_saving,
            "affordability": affordability,
            "down_payment_scenarios": dp_scenarios,
            "hold_years_grid": hold_years_grid,
            "appreciation_results": appreciation_results,
        })

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"scenarios": output_scenarios}, f, indent=2, default=str)

    print("\n" + "=" * 60)
    for s in output_scenarios:
        aff = s["affordability"]
        print(f"\n  [{s['name']}]")
        print(f"  One-time costs:          €{s['one_time_costs']['TOTAL']:>12,.0f}")
        print(f"  Monthly mortgage (gross): €{s['monthly_payment_gross']:>12,.2f}")
        print(f"  Share of net income:      {aff['total_own_pct_net']*100:>6.1f}%  ({aff['verdict']})")
        if aff.get("verdict_after_ruling"):
            print(f"  ...after 30%-ruling ends: {aff['total_own_pct_net_after_ruling']*100:>6.1f}%  ({aff['verdict_after_ruling']})")
        for ar in s["appreciation_results"]:
            be = f"{ar['breakeven_year']:.1f}yr" if ar["breakeven_year"] else "never"
            print(f"    {ar['scenario_name']:<32} breakeven vs rent: {be}")
    print(f"\n  Output written → {OUTPUT_PATH}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
