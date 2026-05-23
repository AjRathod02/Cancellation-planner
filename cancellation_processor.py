"""Cancellation and modification plan logic — operates on in-memory DataFrames only."""

import re
from io import BytesIO
from typing import Optional

import pandas as pd
from dateutil.relativedelta import relativedelta
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows


OUTPUT_COLUMNS = [
    "cancel_by",
    "confirmation",
    "account",
    "resort",
    "checkin",
    "checkout",
    "booking_date",
    "Unit",
    "credits",
    "rented?",
    "action",
]

SKIP_ACCOUNT_PATTERNS: list[str] = []


def _to_date(d):
    if d is None or pd.isna(d):
        return None
    if hasattr(d, "date") and callable(getattr(d, "date")):
        return d.date()
    return d


def _dates_match(ci1, co1, ci2, co2):
    d1, d2 = _to_date(ci1), _to_date(co1)
    d3, d4 = _to_date(ci2), _to_date(co2)
    if d1 is None or d2 is None or d3 is None or d4 is None:
        return False
    return d1 == d3 and d2 == d4


def intervals_intersect(a_start, a_end, b_start, b_end):
    if pd.isna(a_start) or pd.isna(a_end) or pd.isna(b_start) or pd.isna(b_end):
        return False
    return (a_start < b_end) and (b_start < a_end)


def _guest_date_range_from_rented(rented_val):
    if pd.isna(rented_val) or not isinstance(rented_val, str):
        return None
    m = re.search(r"\((\d{1,2})-(\d{1,2})\)", rented_val.strip())
    if not m:
        return None
    try:
        start_day, end_day = int(m.group(1)), int(m.group(2))
        if 1 <= start_day <= 31 and 1 <= end_day <= 31 and start_day <= end_day:
            return (start_day, end_day)
    except (ValueError, TypeError):
        pass
    return None


def _strip_blocked_accounts_from_action(action_str):
    if pd.isna(action_str) or not isinstance(action_str, str):
        return action_str
    s = action_str
    s = re.sub(r"\s*madhu\s+113k\s*\(IS\)\s*", " ", s, flags=re.I)
    s = re.sub(r"\s*81k\s+yr\s*\(IS\)\s*", " ", s, flags=re.I)
    s = re.sub(r"\s*\|\s*\|\s*", " | ", s)
    s = re.sub(r"\s*:-\s*$|\s*\|\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip() or action_str


def _plan_dates_to_timestamps(plan_dates: list[str]) -> list[pd.Timestamp]:
    return [pd.to_datetime(d).normalize() for d in plan_dates]


def format_plan_date_range(plan_dates: list[str]) -> str:
    """Human-readable date range for summaries (e.g. '22-May-2026 and 23-May-2026')."""
    formatted = [pd.to_datetime(d).strftime("%d-%b-%Y") for d in plan_dates]
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]} and {formatted[1]}"
    return ", ".join(formatted[:-1]) + f", and {formatted[-1]}"


def process_cancellation_plan(
    inventory_df: pd.DataFrame,
    confirmed_df: pd.DataFrame,
    credit_df: pd.DataFrame,
    mm_is_df: pd.DataFrame,
    *,
    plan_dates: list[str],
) -> pd.DataFrame:
    """
    Run cancellation/modification analysis on in-memory data.
    Returns output DataFrame (may be empty if no cancellations in the plan window).
    """
    df = inventory_df.copy()
    confirmed_df = confirmed_df.copy()
    credit_df = credit_df.copy()
    mm_is_df = mm_is_df.copy()

    df.columns = df.columns.str.strip()
    confirmed_df.columns = confirmed_df.columns.str.strip()

    if "cancel_by" not in df.columns:
      raise KeyError(
        f"Expected column 'cancel_by' on inventory sheet; got: {list(df.columns)[:20]}"
      )

    if not plan_dates:
      raise ValueError("plan_dates must include at least one date")

    df["cancel_by"] = pd.to_datetime(df["cancel_by"], errors="coerce")
    today = pd.to_datetime("today").normalize()
    plan_ts = _plan_dates_to_timestamps(plan_dates)

    df = df[df["cancel_by"] >= today].copy()

    credit_df.columns = credit_df.columns.str.strip()
    mm_is_df.columns = mm_is_df.columns.str.strip()

    is_resorts = mm_is_df["IS resort"].dropna().str.strip().tolist()
    mm_resorts = mm_is_df["MM resort"].dropna().str.strip().tolist()

    for col in ["MM_Remaining", "Remaining_IS", "Credits"]:
        if col in credit_df.columns:
            credit_df[col] = pd.to_numeric(credit_df[col], errors="coerce").fillna(0)

    filtered_df = df[df["cancel_by"].isin(plan_ts)].copy()

    if filtered_df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    def determine_action(row):
        unit_val = row.get("Unit", "")
        if pd.notna(unit_val) and str(unit_val).strip().lower() == "studio":
            return "Cancel"

        if pd.isna(row["rented?"]) or row["rented?"] == "":
            checkin = pd.to_datetime(row["checkin"], errors="coerce")
            checkout = pd.to_datetime(row["checkout"], errors="coerce")
            booking = pd.to_datetime(row["booking_date"], errors="coerce")
            cancel_by = pd.to_datetime(row["cancel_by"], errors="coerce")

            if pd.isna(checkin) or pd.isna(checkout) or pd.isna(cancel_by):
                return "Date Error - Please Check"

            stay_nights = (checkout - checkin).days
            cancel_gap = (checkin - cancel_by).days
            rd = relativedelta(checkin, booking)
            months = rd.years * 12 + rd.months

            if 0 <= cancel_gap <= 8:
                return "Cancel"
            if months < 10 or stay_nights > 7:
                if stay_nights <= 3:
                    return "Cancel and rebook"
                return "Modify and rebook"
            return "Non- Modifible reservation(cancel and rebook)"
        return "check for drop nights"

    def suggest_accounts(resort, checkin, checkout, required_credits, full_inventory_df, max_accounts=2):
        nights = (checkout - checkin).days
        suggestions = []
        seen = set()

        def no_overlap(acc_name):
            acc_res = full_inventory_df[full_inventory_df["account"] == acc_name].copy()
            acc_res["checkin"] = pd.to_datetime(acc_res["checkin"], errors="coerce")
            acc_res["checkout"] = pd.to_datetime(acc_res["checkout"], errors="coerce")
            for _, r in acc_res.iterrows():
                if intervals_intersect(checkin, checkout, r["checkin"], r["checkout"]):
                    return False
            return True

        def add_if_available(acc, label):
            if any(pat in str(acc).lower() for pat in SKIP_ACCOUNT_PATTERNS):
                return False
            if acc not in seen and no_overlap(acc):
                suggestions.append(f"{acc} ({label})")
                seen.add(acc)
                return len(suggestions) >= max_accounts
            return False

        if resort in is_resorts:
            for acc in credit_df.loc[credit_df["Remaining_IS"] > 0, "account"]:
                if add_if_available(acc, "IS"):
                    return suggestions

        if resort in mm_resorts:
            for acc in credit_df.loc[credit_df["MM_Remaining"] > 0, "account"]:
                if add_if_available(acc, "MM"):
                    return suggestions

        for acc in credit_df["account"]:
            acc_lower = str(acc).strip().lower()
            if "65k joseph" in acc_lower:
                if add_if_available(acc, "Platinum"):
                    return suggestions
            elif "74k" == acc_lower or "74k" in acc_lower.split():
                if nights < 5 and add_if_available(acc, "Platinum"):
                    return suggestions

        for acc in credit_df.loc[credit_df["Credits"] >= required_credits, "account"]:
            if add_if_available(acc, "Credits"):
                return suggestions

        return suggestions

    def match_guest_and_dates(row):
        inventory_checkin = pd.to_datetime(row["checkin"], errors="coerce").normalize()
        inventory_checkout = pd.to_datetime(row["checkout"], errors="coerce").normalize()

        guest_range = _guest_date_range_from_rented(row.get("rented?"))
        if guest_range is not None:
            start_day, end_day = guest_range
            if (
                inventory_checkin.day == start_day
                and inventory_checkout.day == end_day
                and inventory_checkin.month == inventory_checkout.month
                and inventory_checkin.year == inventory_checkout.year
                and inventory_checkout > inventory_checkin
            ):
                return (
                    f"Perfect date: {inventory_checkin.strftime('%d-%b-%y')} to "
                    f"{inventory_checkout.strftime('%d-%b-%y')}"
                )

        if pd.notna(row.get("guest confirmation code")):
            try:
                confirmed_data = confirmed_df[
                    confirmed_df["Confirmation code"].astype(str).str.strip()
                    == str(row["guest confirmation code"]).strip()
                ]

                if not confirmed_data.empty:
                    confirmed_data = confirmed_data.copy()
                    confirmed_data["_ci"] = pd.to_datetime(
                        confirmed_data["Check-in"], errors="coerce"
                    ).dt.normalize()
                    confirmed_data["_co"] = pd.to_datetime(
                        confirmed_data["Check-out"], errors="coerce"
                    ).dt.normalize()

                    exact_mask = confirmed_data.apply(
                        lambda r: _dates_match(r["_ci"], r["_co"], inventory_checkin, inventory_checkout),
                        axis=1,
                    )
                    exact = confirmed_data[exact_mask]
                    if not exact.empty:
                        confirmed_row = exact.iloc[0]
                    else:
                        overlap = confirmed_data[
                            (confirmed_data["_ci"] < inventory_checkout)
                            & (confirmed_data["_co"] > inventory_checkin)
                        ]
                        if not overlap.empty:
                            overlap = overlap.copy()
                            overlap["_gap"] = (
                                (overlap["_ci"] - inventory_checkin).abs().dt.days
                                + (overlap["_co"] - inventory_checkout).abs().dt.days
                            )
                            confirmed_row = overlap.loc[overlap["_gap"].idxmin()]
                        else:
                            confirmed_row = confirmed_data.iloc[0]

                    confirmed_checkin = confirmed_row["_ci"]
                    confirmed_checkout = confirmed_row["_co"]
                    if hasattr(confirmed_checkin, "normalize"):
                        confirmed_checkin = confirmed_checkin.normalize()
                    else:
                        confirmed_checkin = pd.to_datetime(
                            confirmed_row["Check-in"], errors="coerce"
                        ).normalize()
                    if hasattr(confirmed_checkout, "normalize"):
                        confirmed_checkout = confirmed_checkout.normalize()
                    else:
                        confirmed_checkout = pd.to_datetime(
                            confirmed_row["Check-out"], errors="coerce"
                        ).normalize()

                    if (
                        pd.notna(confirmed_checkin)
                        and pd.notna(confirmed_checkout)
                        and _dates_match(
                            confirmed_checkin, confirmed_checkout, inventory_checkin, inventory_checkout
                        )
                    ):
                        return (
                            f"Perfect date: {confirmed_checkin.strftime('%d-%b-%y')} to "
                            f"{confirmed_checkout.strftime('%d-%b-%y')}"
                        )

                    drop_nights = []
                    missing_nights = []

                    if inventory_checkin < confirmed_checkin:
                        current = inventory_checkin
                        while current < confirmed_checkin:
                            drop_nights.append(current.normalize())
                            current += pd.Timedelta(days=1)

                    if inventory_checkout > confirmed_checkout:
                        current = confirmed_checkout
                        while current < inventory_checkout:
                            drop_nights.append(current.normalize())
                            current += pd.Timedelta(days=1)

                    drop_nights = [d for d in drop_nights if inventory_checkin <= d < inventory_checkout]

                    if inventory_checkout < confirmed_checkout:
                        current = inventory_checkout
                        while current < confirmed_checkout:
                            missing_nights.append(current.normalize())
                            current += pd.Timedelta(days=1)

                    if not drop_nights and not missing_nights:
                        return (
                            f"Perfect date: {confirmed_checkin.strftime('%d-%b-%y')} to "
                            f"{confirmed_checkout.strftime('%d-%b-%y')}"
                        )

                    missing_msg = ""
                    if missing_nights:
                        missing_str = ", ".join(
                            [d.strftime("%d-%b-%y") for d in sorted(set(missing_nights))]
                        )
                        guest_date_range = (
                            f"{confirmed_checkin.strftime('%d-%b-%Y')} to "
                            f"{confirmed_checkout.strftime('%d-%b-%Y')}"
                        )
                        night_label = "Night of" if len(missing_nights) == 1 else "Nights of"
                        verb = "is" if len(missing_nights) == 1 else "are"
                        missing_msg = (
                            f"Guest date ({guest_date_range}) {night_label} {missing_str} {verb} missing."
                        )
                        if not drop_nights:
                            return missing_msg

                    drop_nights = sorted(set(drop_nights))
                    groups = []
                    start = drop_nights[0]
                    prev = start
                    for d in drop_nights[1:]:
                        if (d - prev).days == 1:
                            prev = d
                            continue
                        groups.append((start, prev))
                        start = d
                        prev = d
                    groups.append((start, prev))

                    resort = row.get("resort")
                    unit = row.get("Unit")
                    suggestions = []
                    seen_suggestions = set()

                    for gstart, gend in groups:
                        gstart_dt = pd.to_datetime(gstart)
                        gend_dt = pd.to_datetime(gend)
                        after_dt = gend_dt + pd.Timedelta(days=1)

                        cand_mask = (
                            (
                                pd.to_datetime(df["checkin"], errors="coerce").eq(after_dt)
                                | pd.to_datetime(df["checkout"], errors="coerce").eq(gstart_dt)
                            )
                            & (df["resort"] == resort)
                            & (df["Unit"] == unit)
                            & (df["confirmation"] != row["confirmation"])
                            & (df["rented?"].isna() | (df["rented?"] == "") | (df["rented?"] == 0))
                        )

                        cands = df[cand_mask].copy()
                        if not cands.empty:
                            for _, r in cands.iterrows():
                                if any(
                                    pat in str(r.get("account", "")).lower()
                                    for pat in SKIP_ACCOUNT_PATTERNS
                                ):
                                    continue
                                suggestion = f"Account {r['account']} {r['confirmation']}"
                                if suggestion not in seen_suggestions:
                                    suggestions.append(suggestion)
                                    seen_suggestions.add(suggestion)

                    drop_str = ", ".join([d.strftime("%d-%b-%y") for d in drop_nights])
                    prefix = (missing_msg + " ") if missing_msg else ""
                    if suggestions:
                        return f"{prefix}Drop night(s) of {drop_str} -> Stack here: " + " | ".join(
                            suggestions
                        )

                    group_suggestions = []
                    for gstart, gend in groups:
                        start_dt = pd.to_datetime(gstart)
                        end_dt = pd.to_datetime(gend)
                        nights_count = (end_dt - start_dt).days + 1
                        if nights_count < 2:
                            continue
                        gs = start_dt
                        ge = end_dt + pd.Timedelta(days=1)
                        credits_needed = row.get("credits")
                        accs = suggest_accounts(resort, gs, ge, credits_needed, df)
                        if accs:
                            grp_str = f"{gs.strftime('%d-%b-%y')} to {end_dt.strftime('%d-%b-%y')}"
                            group_suggestions.append(f"Book {grp_str} in " + " | ".join(accs))
                    if group_suggestions:
                        return f"{prefix}Drop night(s) of {drop_str} -> " + " ; ".join(group_suggestions)
                    return f"{prefix}Drop night(s) of {drop_str}"

            except Exception:
                return row["action"]

        return row["action"]

    def append_stack_info(row, full_inventory_df):
        if row["action"] == "Cancel":
            return "Cancel"

        if pd.notna(row["rented?"]) and str(row["rented?"]).strip() != "":
            return row["action"]

        resort = row["resort"]
        unit = row["Unit"]
        checkin = pd.to_datetime(row["checkin"], errors="coerce")
        checkout = pd.to_datetime(row["checkout"], errors="coerce")
        required_credits = row["credits"]
        current_nights = (checkout - checkin).days

        temp = full_inventory_df[
            (full_inventory_df["resort"] == resort)
            & (full_inventory_df["Unit"] == unit)
            & (
                (full_inventory_df["rented?"].isna())
                | (full_inventory_df["rented?"] == "")
                | (full_inventory_df["rented?"] == 0)
            )
        ].copy()

        temp = temp[temp["confirmation"] != row["confirmation"]]
        temp["checkin"] = pd.to_datetime(temp["checkin"], errors="coerce")
        temp["checkout"] = pd.to_datetime(temp["checkout"], errors="coerce")

        stack_outputs = []

        for _, r in temp.iterrows():
            if any(pat in str(r.get("account", "")).lower() for pat in SKIP_ACCOUNT_PATTERNS):
                continue
            r_checkin = r["checkin"]
            r_checkout = r["checkout"]
            stack_nights = (r_checkout - r_checkin).days
            stack_type = None

            if r_checkout == checkin:
                stack_type = "BEFORE"
            if r_checkin == checkout:
                stack_type = "AFTER"

            if stack_type:
                if current_nights == 1 and stack_nights in [1, 2, 3]:
                    stack_outputs.append(
                        f"Stack {stack_type} with {r['confirmation']} (Account {r['account']})"
                    )
                else:
                    biacc = f"Book in {r['account']}"
                    if biacc not in stack_outputs:
                        stack_outputs.append(f"Book in {r['account']}")

        if stack_outputs:
            return f"{row['action']} :-" + " | ".join(stack_outputs)

        try:
            accs = suggest_accounts(resort, checkin, checkout, required_credits, full_inventory_df)
            if accs:
                return f"{row['action']} :-Book in " + " | ".join(accs)
        except Exception:
            pass

        return row["action"]

    filtered_df["action"] = filtered_df.apply(determine_action, axis=1)
    filtered_df["action"] = filtered_df.apply(match_guest_and_dates, axis=1)
    filtered_df["action"] = filtered_df.apply(lambda x: append_stack_info(x, df), axis=1)
    filtered_df["action"] = filtered_df["action"].astype(str).apply(_strip_blocked_accounts_from_action)

    return filtered_df[OUTPUT_COLUMNS].copy()


def dataframe_to_excel_bytes(df: pd.DataFrame, sheet_name: str = "Cancellation and modification") -> bytes:
    """Build Excel workbook in memory from result DataFrame."""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(OUTPUT_COLUMNS)
    for r in dataframe_to_rows(df, index=False, header=False):
        ws.append(r)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def build_summary_message(df: pd.DataFrame, *, plan_dates: list[str]) -> str:
    date_range = format_plan_date_range(plan_dates)
    if df.empty:
        return f"No cancellations or modifications for {date_range}."
    return f"Cancellation plan: {len(df)} row(s) for {date_range}."
