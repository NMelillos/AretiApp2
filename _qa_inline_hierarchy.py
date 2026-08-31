import ast
from pathlib import Path


class FakeStreamlit:
    def __init__(self):
        self.session_state = {}


def assert_equal(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name} failed. expected={expected!r} actual={actual!r}")
    print(f"PASS: {name} | {actual!r}")


def extracted_functions(source, *names):
    tree = ast.parse(source)
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {"st": FakeStreamlit()}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
    return namespace


def main():
    source = Path("app.py").read_text(encoding="utf-8")
    namespace = extracted_functions(
        source,
        "_set_executive_selection",
        "_clear_executive_selections",
    )
    state = namespace["st"].session_state
    select = namespace["_set_executive_selection"]
    clear = namespace["_clear_executive_selections"]

    select("group", "Group A")
    assert_equal("group opens", state.get("executive_group"), "Group A")
    state["executive_category"] = "Category A"
    state["executive_subcategory"] = "Subcategory A"
    select("group", "Group B")
    assert_equal("switching group updates selection", state.get("executive_group"), "Group B")
    assert_equal("switching group clears category", "executive_category" in state, False)
    assert_equal("switching group clears subcategory", "executive_subcategory" in state, False)
    select("group", "Group B")
    assert_equal("clicking selected group collapses", "executive_group" in state, False)
    select("group", "Protected group", toggle=False)
    select("group", "Protected group", toggle=False)
    assert_equal(
        "protected detached selection does not gain toggle behavior",
        state.get("executive_group"),
        "Protected group",
    )
    clear("executive_group")

    select(
        "income_charity",
        "Income",
        selection_key="executive_income_charity",
        clear_selection_keys=[
            "executive_income_charity_category",
            "executive_income_charity_subcategory",
        ],
    )
    state["executive_income_charity_category"] = "Income"
    state["executive_income_charity_subcategory"] = "Interest earned"
    select(
        "income_charity",
        "Charity",
        selection_key="executive_income_charity",
        clear_selection_keys=[
            "executive_income_charity_category",
            "executive_income_charity_subcategory",
        ],
    )
    assert_equal("Income switches to Charity", state.get("executive_income_charity"), "Charity")
    assert_equal("type switch clears Item 19 category", "executive_income_charity_category" in state, False)
    assert_equal("type switch clears Item 19 subcategory", "executive_income_charity_subcategory" in state, False)
    clear("executive_income_charity")
    assert_equal("explicit close clears Item 19 type", "executive_income_charity" in state, False)

    renderer_start = source.index("def _render_executive_click_rows")
    renderer_end = source.index("def _executive_selected_transactions_export_sheets")
    renderer_source = source[renderer_start:renderer_end]
    drilldown_start = source.index("def _render_executive_drilldown")
    drilldown_end = source.index("def _render_executive_completeness_check")
    drilldown_source = source[drilldown_start:drilldown_end]
    item19_start = source.index("def _render_income_charity_section")
    item19_end = source.index("def render_executive_report")
    item19_source = source[item19_start:item19_end]

    assert_equal(
        "selected child renders inside the row loop",
        renderer_source.index("for idx, row in enumerate(rows):")
        < renderer_source.index("render_child(row[\"value\"])")
        < renderer_source.index("zero_rows = []"),
        True,
    )
    assert_equal(
        "report hierarchy titles use compact bold report typography",
        '.executive-section-title {' in source
        and 'font-size: 12px;' in source[source.index('.executive-section-title {'):source.index('.executive-section-title {') + 220]
        and 'font-weight: 800;' in source[source.index('.executive-section-title {'):source.index('.executive-section-title {') + 220]
        and 'class=\\\"executive-section-title\\\"' in renderer_source
        and 'st.markdown(f"#### {title}")' not in renderer_source,
        True,
    )
    assert_equal(
        "ordinary hierarchy buttons use pre-rerun callbacks",
        "if inline_selection:" in renderer_source
        and "on_click=_set_executive_selection" in renderer_source,
        True,
    )
    assert_equal(
        "selected hierarchy buttons use the existing green primary state",
        'type="primary" if is_selected else "secondary"' in renderer_source,
        True,
    )
    assert_equal(
        "protected detached renderer keeps its previous explicit lifecycle",
        "toggle=False" in renderer_source and "st.rerun()" in renderer_source,
        True,
    )
    assert_equal(
        "inline hierarchy is enabled at every new nested level",
        drilldown_source.count("inline_selection=True") >= 3
        and item19_source.count("inline_selection=True") >= 3,
        True,
    )
    assert_equal(
        "Reporting Group hierarchy is composed inline",
        "render_child=render_selected_group" in drilldown_source
        and "render_child=lambda category" in drilldown_source
        and "render_child=lambda subcategory" in drilldown_source,
        True,
    )
    assert_equal(
        "Reporting Group context is retained",
        "Reporting group: {group}" in drilldown_source
        and "Category: {category}" in drilldown_source
        and "Subcategory: {subcategory or 'No subcategory'}" in drilldown_source,
        True,
    )
    assert_equal(
        "Item 19 hierarchy is composed inline",
        "render_child=render_selected_type" in item19_source
        and "render_child=lambda category" in item19_source
        and "render_child=lambda subcategory" in item19_source,
        True,
    )
    assert_equal(
        "Item 19 does not use detached selectboxes",
        "st.selectbox(" in item19_source,
        False,
    )
    executive_report_start = source.index("def render_executive_report")
    third_report_start = source.index("def render_third_link_report")
    executive_report_source = source[executive_report_start:third_report_start]
    third_report_source = source[third_report_start:]
    assert_equal(
        "Areti Executive report opts into inline hierarchy",
        "inline_hierarchy=True" in executive_report_source,
        True,
    )
    assert_equal(
        "THIRD report remains on the protected detached hierarchy",
        "inline_hierarchy=True" in third_report_source,
        False,
    )
    print("INLINE_HIERARCHY_QA_COMPLETE")


if __name__ == "__main__":
    main()
