# =========================
# FILE: classification.py
# =========================
from difflib import SequenceMatcher, get_close_matches
import re

import pandas as pd

from db import get_categories, get_category_records


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


def find_existing_category(categories, *candidate_names):
    category_lookup = {str(category).strip().casefold(): category for category in categories}
    for candidate in candidate_names:
        matched = category_lookup.get(str(candidate).strip().casefold())
        if matched:
            return matched
    return None


def auto_rule_category(normalized_description: str, beneficiary: str, transaction_type: str, categories):
    text = normalized_description
    merchant = beneficiary

    bank_fees_category = find_existing_category(categories, "Bank Fees")
    own_funds_category = find_existing_category(categories, "Own Funds")
    subscriptions_category = find_existing_category(categories, "Subscriptions")

    if bank_fees_category and any(x in text for x in ["BANK FEE", "ACCOUNT FEE", "CHARGE", "COMMISSION"]):
        return bank_fees_category, "rule", 1.0

    if own_funds_category and any(x in text for x in ["OWN FUNDS", "INTERNAL TRANSFER", "TRANSFER BETWEEN OWN ACCOUNTS"]):
        return own_funds_category, "rule", 1.0

    if bank_fees_category and transaction_type == "bank_fee":
        return bank_fees_category, "rule", 1.0

    if subscriptions_category and merchant in ["NETFLIX", "SPOTIFY"]:
        return subscriptions_category, "rule", 0.95

    return None, None, 0.0


def tokenize_label(text: str):
    return {
        token
        for token in re.split(r"[^A-Z0-9]+", str(text).upper())
        if len(token) >= 3
    }


def ai_category_guess(normalized_description: str, beneficiary: str, transaction_type: str, category_records):
    text = f"{normalized_description} {beneficiary}".upper()
    text_tokens = tokenize_label(text)
    scores = {}

    for _, row in category_records.iterrows():
        category = str(row.get("category", "")).strip()
        subcategory = str(row.get("subcategory", "")).strip()
        if not category:
            continue

        score = scores.get(category, 0)
        labels = [category]
        if subcategory:
            labels.append(subcategory)

        for label in labels:
            label_text = str(label).upper().strip()
            if label_text and label_text in text:
                score += 3

            label_tokens = tokenize_label(label_text)
            score += len(text_tokens & label_tokens)

        if transaction_type == "bank_fee" and category.casefold() == "bank fees":
            score += 2
        if transaction_type == "transfer" and category.casefold() == "own funds":
            score += 2

        scores[category] = score

    best_category = max(scores, key=scores.get) if scores else None
    best_score = scores.get(best_category, 0) if best_category else 0

    if best_category and best_score > 0:
        confidence = min(0.55 + (0.1 * best_score), 0.9)
        return best_category, "ai", round(confidence, 2)

    return "", "unclassified", 0.0


def classify_transactions(df: pd.DataFrame, memory_df: pd.DataFrame):
    categories = get_categories()
    category_records = get_category_records()
    suggestions = []

    if not categories:
        raise ValueError("No categories are configured. Upload your categories Excel first.")

    for _, row in df.iterrows():
        normalized = row["normalized_description"]
        beneficiary = row["beneficiary"]
        transaction_type = row["transaction_type"]

        rule_cat, rule_match_type, rule_conf = auto_rule_category(
            normalized,
            beneficiary,
            transaction_type,
            categories,
        )
        if rule_cat:
            suggestions.append({
                "suggested_category": rule_cat,
                "suggested_subcategory": "",
                "match_type": rule_match_type,
                "matched_reference": normalized,
                "confidence": rule_conf,
            })
            continue

        exact = exact_match(memory_df, normalized)
        if exact and exact["category"] in categories:
            suggestions.append({
                "suggested_category": exact["category"],
                "suggested_subcategory": "",
                "match_type": "exact",
                "matched_reference": exact["normalized_description"],
                "confidence": 1.0,
            })
            continue

        similar = similar_match(memory_df, normalized, threshold=0.84)
        if similar and similar["category"] in categories:
            suggestions.append({
                "suggested_category": similar["category"],
                "suggested_subcategory": "",
                "match_type": "similar",
                "matched_reference": similar["normalized_description"],
                "confidence": round(float(similar.get("score", 0.85)), 2),
            })
            continue

        ai_cat, ai_type, ai_conf = ai_category_guess(
            normalized,
            beneficiary,
            transaction_type,
            category_records
        )
        suggestions.append({
            "suggested_category": ai_cat,
            "suggested_subcategory": "",
            "match_type": ai_type,
            "matched_reference": "",
            "confidence": ai_conf,
        })

    sugg_df = pd.DataFrame(suggestions)
    out = pd.concat([df.reset_index(drop=True), sugg_df], axis=1)
    return out
