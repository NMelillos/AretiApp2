import pandas as pd

import classification


def assert_true(name, condition, details=""):
    if not condition:
        raise AssertionError(f"{name} failed. {details}")
    print(f"PASS: {name}" + (f" | {details}" if details else ""))


def main():
    classification.get_categories = lambda: [
        "Lifestyle",
        "Technology",
        "Bank commissions and fees",
        "UNIDENTIFIED EXPENSES",
    ]

    memory = pd.DataFrame(
        [
            {
                "normalized_description": "REPLIT SUBSCRIPTION JUNE",
                "original_description": "Replit subscription June",
                "transaction_type": "card_payment",
                "category": "Technology",
                "subcategory": "Software",
                "times_seen": 1,
                "last_seen": "2026-06-01",
            },
            {
                "normalized_description": "REPLIT AI MONTHLY PLAN",
                "original_description": "Replit AI monthly plan",
                "transaction_type": "card_payment",
                "category": "Technology",
                "subcategory": "Software",
                "times_seen": 1,
                "last_seen": "2026-07-01",
            },
        ]
    )
    rows = pd.DataFrame(
        [
            {
                "normalized_description": "REPLIT CLOUD SUBSCRIPTION",
                "beneficiary": "",
                "transaction_type": "card_payment",
                "Amount": -25.0,
            },
            {
                "normalized_description": "COMMISSION",
                "beneficiary": "",
                "transaction_type": "bank_fee",
                "Amount": -12.0,
            },
        ]
    )

    classified = classification.classify_transactions(rows, memory)
    replit = classified.iloc[0]
    commission = classified.iloc[1]

    assert_true(
        "known recurring merchant predicts subcategory",
        replit["suggested_category"] == "Technology" and replit["suggested_subcategory"] == "Software",
        f"{replit['suggested_category']} / {replit['suggested_subcategory']}",
    )
    assert_true(
        "commission stays in bank-fee category",
        commission["suggested_category"] == "Bank commissions and fees",
        str(commission["suggested_category"]),
    )
    assert_true(
        "commission is not lifestyle",
        commission["suggested_category"] != "Lifestyle",
        str(commission["suggested_category"]),
    )
    print("QA_COMPLETE")


if __name__ == "__main__":
    main()
