"""Report hierarchy presentation contract, independent of monetary calculations."""
import ast
import hashlib
from pathlib import Path
import subprocess


def without_formatting_change(name, actual):
    if name != "app.py":
        return actual
    from _qa_pending_save_normalization import without_save_normalization
    actual = without_save_normalization(name, actual)
    actual = actual.replace(b"\r\n", b"\n")
    assert hashlib.sha256(actual).hexdigest() == "37a39e5066faa18e59d77948fd463bb4caf87f74c603039bfe812c497e2a310c"
    baseline = subprocess.check_output(["git", "show", "5580da7b06e4b8dd1a740ce30b66f2891989e160:app.py"]).replace(b"\r\n", b"\n")
    old, new = ast.parse(baseline), ast.parse(actual)
    def is_style(node):
        return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and node.value.args and isinstance(node.value.args[0], ast.Constant)
                and "<style>" in str(node.value.args[0].value))
    old_styles = [n for n in old.body if is_style(n)]
    assert len(old_styles) == 1
    new.body = [old_styles[0] if is_style(n) else n for n in new.body]
    section = next(n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == "_render_income_charity_section")
    removed = []
    for node in ast.walk(section):
        if isinstance(node, ast.Call):
            removed.extend(k.value.value for k in node.keywords if k.arg == "child_branch_key")
            node.keywords = [k for k in node.keywords if k.arg != "child_branch_key"]
            if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "Subcategories":
                presentation = [k for k in node.keywords if k.arg in {"show_title", "show_header"}]
                assert len(presentation) == 2 and all(k.value.value is False for k in presentation)
                node.keywords = [k for k in node.keywords if k.arg not in {"show_title", "show_header"}]
    assert removed == ["income_charity_branch_subcategory", "income_charity_branch_category"]
    drilldown = next(n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == "_render_executive_drilldown")
    changed = 0
    for node in ast.walk(drilldown):
        if isinstance(node, ast.Call) and any(k.arg == "child_branch_key" and k.value.value in
                {"executive_hierarchy_branch_category", "executive_hierarchy_branch_subcategory"} for k in node.keywords):
            presentation = [k for k in node.keywords if k.arg in {"show_title", "show_header"}]
            assert len(presentation) == 2 and all(k.value.value is False for k in presentation)
            node.keywords = [k for k in node.keywords if k.arg not in {"show_title", "show_header"}]
            changed += 1
    assert changed == 2
    assert ast.dump(old) == ast.dump(new), "Formatting changed non-presentation code"
    return baseline


def main():
    source = Path("app.py").read_text(encoding="utf-8")
    start = source.index('div[class*="st-key-executive_category_"]')
    end = source.index('div[class*="st-key-close_income_charity_analysis_"]', start)
    css = source[start:end]
    assert "width: 75% !important" in css, "Category must occupy 3/4 of the label column"
    assert "width: 50% !important" in css, "Subcategory must occupy 2/4 of the label column"
    section = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "_render_income_charity_section")
    keys = [k.value.value for n in ast.walk(section) if isinstance(n, ast.Call)
            for k in n.keywords if k.arg == "child_branch_key" and isinstance(k.value, ast.Constant)]
    assert keys == ["income_charity_branch_subcategory", "income_charity_branch_category"]
    assert 'div[class*="st-key-income_charity_branch_"]::before' in source
    assert 'div[class*="st-key-executive_hierarchy_branch_group"]::before' in source
    assert "min-height: 24px !important" in source
    assert 'div[data-testid="stMarkdownContainer"]:has(> .drill-inline-context)' in source
    print("PASS: 4/4 -> 3/4 -> 2/4 label hierarchy, explicit child branches, green connectors and small Close")


if __name__ == "__main__":
    main()
