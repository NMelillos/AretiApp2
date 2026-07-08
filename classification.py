from difflib import SequenceMatcher, get_close_matches
import re

import pandas as pd

from db import get_categories


def similarity(a, b):
    return SequenceMatcher(None, str(a), str(b)).ratio()


def _fallback_category(categories):
    if "UNIDENTIFIED EXPENSES" in categories:
        return "UNIDENTIFIED EXPENSES"
    return categories[0] if categories else ""


def _category_lookup(categories):
    return {str(category).strip().casefold(): category for category in categories}


def _active_category(category, categories):
    if not category:
        return ""
    lookup = _category_lookup(categories)
    return lookup.get(str(category).strip().casefold(), "")


MERCHANT_SIGNATURE_STOP_WORDS = {
    "ACCOUNT",
    "ATM",
    "AUTH",
    "BANK",
    "CARD",
    "CASH",
    "COMMISSION",
    "CONTINUE",
    "CREDIT",
    "CY",
    "DEBIT",
    "EUR",
    "FEE",
    "FEES",
    "FROM",
    "GBP",
    "IBANK",
    "INTERNET",
    "INWARD",
    "MAINTENANCE",
    "OUR",
    "OUT",
    "OUTWARD",
    "PAGE",
    "PAYMENT",
    "POS",
    "PROCESSING",
    "PURCHASE",
    "REF",
    "STATEMENT",
    "TIPS",
    "TO",
    "TRACE",
    "TRANSFER",
    "USD",
    "WITHDRAWAL",
}
MERCHANT_BRANDS = {
    "ADOBE": "ADOBE",
    "AMBER AND JOE": "AMBER JOE",
    "AMBER JOE": "AMBER JOE",
    "BOLT": "BOLT",
    "KREA AI": "KREA AI",
    "KREA": "KREA AI",
    "REPLIT": "REPLIT",
    "WOLT": "WOLT",
}
MERCHANT_HISTORY_MIN_MATCHES = 3
MERCHANT_HISTORY_CONFIDENCE_THRESHOLD = 0.80


def _merchant_signature(description):
    text = re.sub(r"[^A-Z0-9\s&]", " ", str(description or "").upper())
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    for brand, signature in MERCHANT_BRANDS.items():
        if brand in text:
            return signature
    tokens = []
    for token in text.split():
        if token in MERCHANT_SIGNATURE_STOP_WORDS:
            continue
        if any(char.isdigit() for char in token):
            continue
        if len(token) < 3:
            continue
        tokens.append(token)
        if len(tokens) == 3:
            break
    return " ".join(tokens)


def exact_match(memory_df, normalized_description):
    if memory_df.empty or not normalized_description:
        return None
    matches = memory_df[memory_df["normalized_description"] == normalized_description]
    if matches.empty:
        return None
    return matches.sort_values("times_seen", ascending=False).iloc[0].to_dict()


def similar_match(memory_df, normalized_description, transaction_type="", threshold=0.90):
    if memory_df.empty or not normalized_description:
        return None
    search_df = memory_df.copy()
    if transaction_type and "transaction_type" in search_df.columns:
        same_type = search_df["transaction_type"].fillna("").astype(str) == str(transaction_type)
        if same_type.any():
            search_df = search_df[same_type].copy()

    if search_df.empty:
        return None

    descriptions = search_df["normalized_description"].dropna().astype(str).unique().tolist()
    candidates = get_close_matches(normalized_description, descriptions, n=5, cutoff=threshold)
    if not candidates:
        scored = [(desc, similarity(normalized_description, desc)) for desc in descriptions]
        scored = [item for item in scored if item[1] >= threshold]
        if not scored:
            return None
        scored.sort(key=lambda item: item[1], reverse=True)
        candidates = [scored[0][0]]

    rows = search_df[search_df["normalized_description"].isin(candidates)].copy()
    if rows.empty:
        return None
    rows["score"] = rows["normalized_description"].apply(lambda desc: similarity(normalized_description, desc))
    rows = rows.sort_values(["score", "times_seen"], ascending=[False, False])
    return rows.iloc[0].to_dict()


def conservative_rule_category(normalized_description, categories, amount=0, transaction_type=""):
    text = str(normalized_description).upper()
    own_funds = _active_category("Own funds", categories)
    if own_funds and (
        transaction_type == "own_funds"
        or any(token in text for token in ["OWN FUNDS", "OWN ACCOUNT", "INTERNAL TRANSFER"])
    ):
        return own_funds, "rule", 0.98
    if own_funds and float(amount or 0) > 0 and any(
        token in text
        for token in ["TRANSFER FROM", "TOP-UP", "BANK CREDIT ADVICE", "FROM ", "CREDIT ADVICE"]
    ):
        return own_funds, "rule", 0.92
    if "Interest paid (including Credit Line)" in categories and any(
        token in text for token in ["INTEREST", "CREDIT LINE"]
    ):
        return "Interest paid (including Credit Line)", "rule", 0.95
    bank_charge = _active_category("Interest, fees and charges", categories)
    if bank_charge and any(
        token in text
        for token in [
            "BANK FEE",
            "ACCOUNT FEE",
            "PROCESSING FEE",
            "PROCESSING FEES",
            "TRANSFER COMMISSION",
            "EXCHANGE COMMISSION",
            "COMMISSION",
            "MAINTENANCE FEE",
            "MAINTENANCE FEES",
            "PLAN FEE",
            "ULTRA PLAN FEE",
            " FEES",
            " FEE",
        ]
    ):
        return bank_charge, "rule", 0.95
    return None, None, 0.0


def keyword_category_guess(normalized_description, beneficiary, categories):
    text = f"{normalized_description} {beneficiary}".upper()
    rules = {
        "Subscriptions": ["NETFLIX", "SPOTIFY", "DISNEY", "SUBSCRIPTION", "YOUTUBE", "REPLIT"],
        "Food, Groceries, Wine, Coffee": ["COFFEE", "CAFE", "SUPERMARKET", "GROCERY", "BAKERY", "RESTAURANT", "WOLT", "AMBER JOE"],
        "Transportation": ["UBER", "BOLT", "TAXI", "PETROL", "FUEL", "PARKING", "BUS", "TRAIN"],
        "Bills (EAC, Water, Internet, Telephony)": ["EAC", "ELECTRIC", "WATER", "CYTA", "TELEPHONE", "INTERNET"],
        "Medical, insurance and healthcare": ["DOCTOR", "PHARMACY", "MEDICAL", "HOSPITAL", "CLINIC"],
        "Insurance": ["INSURANCE"],
        "Education and learning": ["SCHOOL", "COURSE", "EDUCATION", "LESSON"],
        "Childcare, School & Children Activities": ["SCHOOL", "CHILD", "KIDS", "FOOTBALL", "SWIMMING", "THERAPY"],
        "Business trips & business exps": ["HOTEL", "BOOKING", "AIRLINE", "AEGEAN", "RYANAIR"],
        "Walt Disney house expenses": ["DISNEY HOUSE"],
    }
    scores = {category: 0 for category in categories}
    for category, keywords in rules.items():
        if category not in scores:
            continue
        for keyword in keywords:
            if keyword in text:
                scores[category] += 1

    best_category = max(scores, key=scores.get) if scores else ""
    best_score = scores.get(best_category, 0)
    if best_score > 0:
        return best_category, "suggestion", min(0.55 + best_score * 0.1, 0.85)
    return _fallback_category(categories), "new", 0.0


def _memory_indexes(memory_df):
    if memory_df.empty:
        return {}, {}, {}, [], {}, {}

    memory = memory_df.copy()
    for column in [
        "normalized_description",
        "transaction_type",
        "category",
        "subcategory",
        "original_description",
        "last_seen",
    ]:
        if column not in memory.columns:
            memory[column] = ""
    if "times_seen" not in memory.columns:
        memory["times_seen"] = 0

    memory["normalized_description"] = memory["normalized_description"].fillna("").astype(str)
    memory["transaction_type"] = memory["transaction_type"].fillna("").astype(str)
    memory["times_seen"] = pd.to_numeric(memory["times_seen"], errors="coerce").fillna(0)
    memory = memory[memory["normalized_description"] != ""].copy()
    memory = memory.sort_values(["times_seen", "last_seen"], ascending=[False, False])

    exact = {}
    best_by_type_desc = {}
    descriptions_by_type = {}
    all_descriptions = []
    best_by_signature = {}
    signature_history = {}

    for _, row in memory.iterrows():
        item = row.to_dict()
        desc = item["normalized_description"]
        tx_type = item.get("transaction_type", "")
        signature = _merchant_signature(desc)
        if desc not in exact:
            exact[desc] = item
            all_descriptions.append(desc)
        if signature and signature not in best_by_signature:
            best_by_signature[signature] = item
        if signature:
            signature_history.setdefault(signature, []).append(item)
        best_by_type_desc.setdefault(tx_type, {})
        descriptions_by_type.setdefault(tx_type, [])
        if desc not in best_by_type_desc[tx_type]:
            best_by_type_desc[tx_type][desc] = item
            descriptions_by_type[tx_type].append(desc)

    return exact, best_by_type_desc, descriptions_by_type, all_descriptions, best_by_signature, signature_history


def _consistent_merchant_history(signature, signature_history, categories):
    rows = signature_history.get(signature, [])
    if not rows:
        return None

    category_lookup = _category_lookup(categories)
    totals = {}
    references = {}
    total_seen = 0.0
    for row in rows:
        category = category_lookup.get(str(row.get("category", "")).strip().casefold(), "")
        subcategory = str(row.get("subcategory", "") or "").strip()
        if not category or not subcategory:
            continue
        weight = float(row.get("times_seen", 0) or 0) or 1.0
        key = (category, subcategory)
        totals[key] = totals.get(key, 0.0) + weight
        total_seen += weight
        references.setdefault(key, row)

    if total_seen < MERCHANT_HISTORY_MIN_MATCHES or not totals:
        return None

    best_key, best_seen = max(totals.items(), key=lambda item: item[1])
    confidence_ratio = best_seen / total_seen if total_seen else 0
    if best_seen < MERCHANT_HISTORY_MIN_MATCHES or confidence_ratio < MERCHANT_HISTORY_CONFIDENCE_THRESHOLD:
        return None

    reference = dict(references[best_key])
    reference["category"] = best_key[0]
    reference["subcategory"] = best_key[1]
    reference["score"] = round(min(0.99, 0.82 + confidence_ratio * 0.15), 2)
    reference["history_count"] = int(best_seen)
    return reference


def _similar_from_index(
    normalized_description,
    transaction_type,
    best_by_type_desc,
    descriptions_by_type,
    exact_index,
    all_descriptions,
    threshold=0.90,
):
    if not normalized_description:
        return None

    descriptions = descriptions_by_type.get(str(transaction_type), [])
    row_lookup = best_by_type_desc.get(str(transaction_type), {})
    if not descriptions:
        descriptions = all_descriptions
        row_lookup = exact_index
    if not descriptions:
        return None

    candidates = get_close_matches(normalized_description, descriptions, n=3, cutoff=threshold)
    if not candidates:
        return None

    best_desc = max(candidates, key=lambda desc: similarity(normalized_description, desc))
    row = dict(row_lookup[best_desc])
    row["score"] = similarity(normalized_description, best_desc)
    return row


def classify_transactions(df, memory_df):
    categories = get_categories()
    category_lookup = _category_lookup(categories)
    (
        exact_index,
        best_by_type_desc,
        descriptions_by_type,
        all_descriptions,
        best_by_signature,
        signature_history,
    ) = _memory_indexes(memory_df)
    suggestions = []

    def active_category(category):
        if not category:
            return ""
        return category_lookup.get(str(category).strip().casefold(), "")

    for _, row in df.iterrows():
        normalized = row.get("normalized_description", "")
        beneficiary = row.get("beneficiary", "")
        transaction_type = row.get("transaction_type", "")
        amount = row.get("Amount", row.get("amount", 0))

        exact = exact_index.get(str(normalized), None)
        exact_category = active_category(exact.get("category", "")) if exact else ""
        if exact and exact_category:
            suggestions.append({
                "suggested_category": exact_category,
                "suggested_subcategory": exact.get("subcategory", ""),
                "match_type": "exact",
                "matched_reference": exact.get("original_description") or exact["normalized_description"],
                "confidence": 1.0,
            })
            continue

        merchant_signature = _merchant_signature(normalized)
        historical = (
            _consistent_merchant_history(merchant_signature, signature_history, categories)
            if merchant_signature
            else None
        )
        historical_category = active_category(historical.get("category", "")) if historical else ""
        if historical and historical_category:
            suggestions.append({
                "suggested_category": historical_category,
                "suggested_subcategory": historical.get("subcategory", ""),
                "match_type": "similar",
                "matched_reference": (
                    historical.get("original_description")
                    or historical.get("normalized_description")
                    or f"Consistent merchant history: {merchant_signature}"
                ),
                "confidence": float(historical.get("score", 0.94) or 0.94),
            })
            continue

        merchant = best_by_signature.get(merchant_signature) if merchant_signature else None
        merchant_category = active_category(merchant.get("category", "")) if merchant else ""
        if merchant and merchant_category:
            suggestions.append({
                "suggested_category": merchant_category,
                "suggested_subcategory": "",
                "match_type": "similar",
                "matched_reference": merchant.get("original_description") or merchant["normalized_description"],
                "confidence": 0.78,
            })
            continue

        rule_category, match_type, confidence = conservative_rule_category(
            normalized,
            categories,
            amount=amount,
            transaction_type=transaction_type,
        )
        if rule_category:
            suggestions.append({
                "suggested_category": rule_category,
                "suggested_subcategory": "",
                "match_type": match_type,
                "matched_reference": normalized,
                "confidence": confidence,
            })
            continue

        similar = _similar_from_index(
            str(normalized),
            str(transaction_type),
            best_by_type_desc,
            descriptions_by_type,
            exact_index,
            all_descriptions,
        )
        similar_category = active_category(similar.get("category", "")) if similar else ""
        if similar and similar_category:
            suggestions.append({
                "suggested_category": similar_category,
                "suggested_subcategory": similar.get("subcategory", ""),
                "match_type": "similar",
                "matched_reference": similar.get("original_description") or similar["normalized_description"],
                "confidence": round(float(similar.get("score", 0.90)), 2),
            })
            continue

        category, match_type, confidence = keyword_category_guess(normalized, beneficiary, categories)
        suggestions.append({
            "suggested_category": category,
            "suggested_subcategory": "",
            "match_type": match_type,
            "matched_reference": "",
            "confidence": confidence,
        })

    return pd.concat([df.reset_index(drop=True), pd.DataFrame(suggestions)], axis=1)
