import ast
import re
from pathlib import Path

import pandas as pd


APP_SOURCE = Path(__file__).with_name("app.py").read_text(encoding="utf-8")


def load_function(name, namespace):
    module = ast.parse(APP_SOURCE)
    function = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == name
    )
    code = compile(ast.Module(body=[function], type_ignores=[]), "app.py", "exec")
    exec(code, namespace)
    return namespace[name]


def check(condition, message, detail=None):
    if not condition:
        raise AssertionError(f"FAIL: {message} | {detail}")
    print(f"PASS: {message}" + (f" | {detail}" if detail is not None else ""))


namespace = {
    "pd": pd,
    "_refresh_category_pair_derived_columns": lambda frame, _categories: frame.copy(),
}
changed_rows = load_function("_changed_transaction_editor_rows", namespace)

baseline = pd.DataFrame([
    {
        "id": row_id,
        "category": "Original",
        "subcategory": "",
        "reviewed": True,
        "status": "reviewed",
    }
    for row_id in range(1, 31)
])
edited = baseline.copy()
edited["category"] = "Changed"

selected = changed_rows(
    baseline,
    edited,
    pd.DataFrame(),
    ["category", "subcategory", "reviewed", "status"],
)
check(len(selected) == 30, "30 rapid edits are selected for one batch save", len(selected))
check(selected["id"].nunique() == 30, "every changed transaction ID is selected once")

unchanged = changed_rows(
    baseline,
    baseline.copy(),
    pd.DataFrame(),
    ["category", "subcategory", "reviewed", "status"],
)
check(unchanged.empty, "unchanged rows are not written")

reordered = baseline.iloc[::-1].reset_index(drop=True)
reordered.loc[reordered["id"].isin([2, 17, 29]), "subcategory"] = "New subcategory"
selected_reordered = changed_rows(
    baseline,
    reordered,
    pd.DataFrame(),
    ["category", "subcategory", "reviewed", "status"],
)
check(
    sorted(selected_reordered["id"].tolist()) == [2, 17, 29],
    "changes are matched by transaction ID after sorting",
    sorted(selected_reordered["id"].tolist()),
)

whitespace = baseline.copy()
whitespace.loc[0, "category"] = " Original "
selected_whitespace = changed_rows(
    baseline,
    whitespace,
    pd.DataFrame(),
    ["category", "subcategory", "reviewed", "status"],
)
check(selected_whitespace.empty, "display whitespace does not create a false write")

app_compact = " ".join(APP_SOURCE.split())
check(
    'with st.form(f"{executive_editor_key}_batch_form"' in APP_SOURCE,
    "Executive editor defers widget changes inside a form",
)
check(
    'with st.form(f"{db_editor_key}_batch_form"' in APP_SOURCE,
    "Database editor defers widget changes inside a form",
)
check(
    "key=executive_editor_key, on_change=_capture_data_editor_state" not in app_compact,
    "Executive editor has no per-dropdown callback",
)
check(
    "key=db_editor_key, on_change=_capture_data_editor_state" not in app_compact,
    "Database editor has no per-dropdown callback",
)
check(
    'render_wrapped_descriptions(detail_view, expanded=False)' in APP_SOURCE,
    "Executive descriptions stay collapsed before batch editing",
)
check(
    len(re.findall(r"render_category_correction_panel\([\s\S]*?expanded=False,\s*inline=False,\s*\)", APP_SOURCE)) >= 3,
    "Single-row correction tools stay optional on all three editing surfaces",
)
check(
    all(
        call in APP_SOURCE
        for call in [
            'render_bulk_categorise_panel(detail_view, categories, "executive_detail", expanded=False, inline=False)',
            'render_bulk_categorise_panel(pending_view, categories, "pending", expanded=False, inline=False)',
            'render_bulk_categorise_panel(db_view, categories, "database", expanded=False, inline=False)',
        ]
    ),
    "Bulk tools stay optional so the deferred batch grid is the primary workflow",
)

print("Deferred editor QA passed.")
