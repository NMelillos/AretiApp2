# =========================
# FILE: classification.py
# =========================
from difflib import SequenceMatcher, get_close_matches

import pandas as pd

from db import get_categories


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def exact_match(memory_df: pd.DataFrame, normalized_description: str):
    if memory_df.empty or not normalized_description:
        return None

    matches = memory_df[memory_df["normalized_description"] == normalized_description]
    if matches.empty:
        return None

    matches = matches.sort_values(by="times_seen", ascending=False)
    return matches.iloc[0].to_dict()


def similar_match(memory_df: pd.DataFrame, normalized_description: str, threshold: float = 0.84):
    if memory_df.empty or not normalized_description:
        return None

    memory_descriptions = (
        memory_df["normalized_description"].dropna().astype(str).unique().tolist()
    )

    candidates = get_close_matches(
        normalized_description,
        memory_descriptions,
        n=5,
        cutoff=threshold
    )

    if not candidates:
        scored = []
        for mem_desc in memory_descriptions:
            score = similarity(normalized_description, mem_desc)
            if score >= threshold:
                scored.append((mem_desc, score))

        if not scored:
            return None

        scored.sort(key=lambda x: x[1], reverse=True)
        candidates = [scored[0][0]]

    candidate_rows = memory_df[
        memory_df["normalized_description"].isin(candidates)
    ].copy()

    if candidate_rows.empty:
        return None

    candidate_rows["score"] = candidate_rows["normalized_description"].apply(
        lambda x: similarity(normalized_description, x)
    )
    candidate_rows = candidate_rows.sort_values(
        by=["score", "times_seen"],
        ascending=[False, False]
    )

    return candidate_rows.iloc[0].to_dict()


def auto_rule_category(normalized_description: str, beneficiary: str, transaction_type: str):
    text = normalized_description
    merchant = beneficiary

    if any(x in text for x in ["BANK FEE", "ACCOUNT FEE", "CHARGE", "COMMISSION"]):
        return "Bank Fees", "rule", 1.0

    if any(x in text for x in ["OWN FUNDS", "INTERNAL TRANSFER", "TRANSFER BETWEEN OWN ACCOUNTS"]):
        return "Own Funds", "rule", 1.0

    if transaction_type == "bank_fee":
        return "Bank Fees", "rule", 1.0

    if merchant in ["NETFLIX", "SPOTIFY"]:
        return "Subscriptions", "rule", 0.95

    return None, None, 0.0


def ai_category_guess(normalized_description: str, beneficiary: str, transaction_type: str, categories):
    text = f"{normalized_description} {beneficiary}".upper()

    scoring_rules = {
        "Subscriptions": ["NETFLIX", "SPOTIFY", "DISNEY", "PRIME VIDEO", "YOUTUBE PREMIUM", "SUBSCRIPTION"],
        "Bank Fees": ["BANK FEE", "ACCOUNT FEE", "CHARGE", "COMMISSION"],
        "Own Funds": ["OWN FUNDS", "INTERNAL TRANSFER", "TRANSFER BETWEEN OWN ACCOUNTS"],
        "Utilities": ["ELECTRIC", "ELECTRICITY", "WATER", "UTILITY", "CYTA", "EAC"],
        "Rent": ["RENT", "LANDLORD", "PROPERTY RENT"],
        "Salaries": ["SALARY", "PAYROLL", "WAGES"],
        "Office Supplies": ["OFFICE", "STATIONERY", "SUPPLIES", "PAPER", "TONER"],
        "Travel": ["UBER", "BOLT", "AIRBNB", "BOOKING", "RYANAIR", "AEGEAN", "HOTEL", "TRAVEL"],
        "Insurance": ["INSURANCE", "AXA", "ALLIANZ", "GENERAL INSURANCE"],
        "Taxes": ["TAX", "VAT", "INCOME TAX", "SOCIAL INSURANCE"],
        "Professional Services": ["LAWYER", "LEGAL", "ACCOUNTANT", "CONSULTING", "ADVISORY"],
        "Online Shopping": ["AMAZON", "EBAY", "ALIEXPRESS", "ONLINE SHOP", "MARKETPLACE"],
        "Entertainment": ["CINEMA", "THEATRE", "EVENT", "TICKETMASTER", "ENTERTAINMENT"],
        "Marketing": ["FACEBOOK ADS", "GOOGLE ADS", "ADVERTISING", "MARKETING", "META ADS"],
        "Software": ["MICROSOFT", "GOOGLE", "ADOBE", "ZOOM", "DROPBOX", "NOTION", "SOFTWARE", "CANVA"],
        "Telephone": ["PHONE", "TELEPHONE", "MOBILE", "VODAFONE", "CYTA MOBILE"],
    }

    scores = {cat: 0 for cat in categories}

    for category, keywords in scoring_rules.items():
        if category not in categories:
            continue
        for keyword in keywords:
            if keyword in text:
                scores[category] += 1

    if transaction_type == "bank_fee" and "Bank Fees" in categories:
        scores["Bank Fees"] += 2

    if transaction_type == "transfer" and "Own Funds" in categories:
        scores["Own Funds"] += 2

    best_category = max(scores, key=scores.get) if scores else None
    best_score = scores.get(best_category, 0) if best_category else 0

    if best_category and best_score > 0:
        confidence = min(0.55 + (0.1 * best_score), 0.9)
        return best_category, "ai", round(confidence, 2)

    fallback = "Other" if "Other" in categories else categories[0]
    return fallback, "ai", 0.35


def classify_transactions(df: pd.DataFrame, memory_df: pd.DataFrame):
    categories = get_categories()
    suggestions = []

    for _, row in df.iterrows():
        normalized = row["normalized_description"]
        beneficiary = row["beneficiary"]
        transaction_type = row["transaction_type"]

        rule_cat, rule_match_type, rule_conf = auto_rule_category(
            normalized,
            beneficiary,
            transaction_type
        )
        if rule_cat:
            suggestions.append({
                "suggested_category": rule_cat,
                "match_type": rule_match_type,
                "matched_reference": normalized,
                "confidence": rule_conf,
            })
            continue

        exact = exact_match(memory_df, normalized)
        if exact:
            suggestions.append({
                "suggested_category": exact["category"],
                "match_type": "exact",
                "matched_reference": exact["normalized_description"],
                "confidence": 1.0,
            })
            continue

        similar = similar_match(memory_df, normalized, threshold=0.84)
        if similar:
            suggestions.append({
                "suggested_category": similar["category"],
                "match_type": "similar",
                "matched_reference": similar["normalized_description"],
                "confidence": round(float(similar.get("score", 0.85)), 2),
            })
            continue

        ai_cat, ai_type, ai_conf = ai_category_guess(
            normalized,
            beneficiary,
            transaction_type,
            categories
        )
        suggestions.append({
            "suggested_category": ai_cat,
            "match_type": ai_type,
            "matched_reference": "",
            "confidence": ai_conf,
        })

    sugg_df = pd.DataFrame(suggestions)
    out = pd.concat([df.reset_index(drop=True), sugg_df], axis=1)
    return out
