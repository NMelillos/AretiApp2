"""Updated THIRD presentation requirement with exact non-layout protection."""
import ast
from pathlib import Path
import subprocess

TOKEN = b"            show_group_total=True,\n            inline_hierarchy=True,\n"


def without_third_nested(source):
    source = source.replace(b"\r\n", b"\n")
    baseline = subprocess.check_output(["git", "show", "dc5d9c83c3e527808072107791ba1f22828bb02f:app.py"]).replace(b"\r\n", b"\n")
    if TOKEN in source:
        assert source.count(TOKEN) == 1
        source = source.replace(TOKEN, b"            show_group_total=True,\n", 1)
    assert source == baseline, "Application changed beyond the exact THIRD presentation flag"
    return source


def main():
    source = Path("app.py").read_bytes()
    tree = ast.parse(source)
    third = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render_third_link_report")
    call, = [n for n in ast.walk(third) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_render_executive_drilldown"]
    options = {k.arg: ast.literal_eval(k.value) for k in call.keywords if isinstance(k.value, ast.Constant)}
    assert options.get("inline_hierarchy") is True, "THIRD must nest children under their own reporting group"
    assert options["read_only"] is True and options["show_group_total"] is True
    from _qa_income_save_diagnostic import without_income_save_diagnostic
    source = without_income_save_diagnostic("app.py", source)
    without_third_nested(source)
    print("PASS: THIRD inline hierarchy, read-only controls and exact unchanged financial/scope code")


if __name__ == "__main__":
    main()
