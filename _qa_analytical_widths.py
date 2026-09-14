"""Exact layout-only compatibility and scoped analytical sizing regression."""
import ast
from contextlib import nullcontext
from pathlib import Path
import subprocess

BASELINE = "becf943051d732741ef7b50335f642bbf444897d"


def without_analytical_sizing(name, source):
    source = source.replace(b"\r\n", b"\n")
    if name != "app.py":
        return source
    baseline = subprocess.check_output(["git", "show", BASELINE + ":app.py"]).replace(b"\r\n", b"\n")
    wrapper = (b"    arguments = locals().copy()\n"
               b"    import analytical_layout\n"
               b"    if not analytical_layout.active():\n"
               b"        return analytical_layout.render(st, _render_executive_click_rows, arguments)\n")
    replacement = b"analytical_layout.columns(st, widths, ai=ai_prompts is not None, full=show_all_months)"
    assert source.count(wrapper) == 1
    assert source.count(replacement) == 2
    restored = source.replace(wrapper, b"", 1).replace(replacement, b"st.columns(widths)")
    assert restored == baseline, "An application change exceeds the exact analytical sizing patch"
    return restored


def main():
    import analytical_layout as layout
    without_analytical_sizing("app.py", Path("app.py").read_bytes())
    for name in ("db.py", "reporting.py", "parsers.py"):
        if Path(name).exists():
            prior = subprocess.check_output(["git", "show", BASELINE + ":" + name])
            assert Path(name).read_bytes().replace(b"\r\n", b"\n") == prior.replace(b"\r\n", b"\n")
    class Surface:
        def __init__(self):
            self.styles = []
            self.weights = []
        def container(self, **kwargs):
            return nullcontext()
        def markdown(self, text, **kwargs):
            self.styles.append(text)
        def columns(self, weights):
            self.weights.append(tuple(weights))
            return weights
    weights = [1.75, 1, .85, 1, 1, 1, 1.25, .95, 1.25, 1.25, .95, 1.15]
    surface = Surface()
    def render(level):
        assert layout.active()
        layout.columns(surface, weights)
        layout.columns(surface, weights)
    layout.render(surface, render, {"level": "group"})
    assert not layout.active()
    assert surface.weights == [tuple(weights), tuple(weights)]
    css = "\n".join(surface.styles)
    for token in ("container-type: inline-size", "overflow-x: auto", "grid-template-columns",
                  "calc(2 *", "calc(1.5 *", "left:", "::before"):
        assert token in css
    assert "font-size" not in css and "line-height" not in css
    def failure(level):
        raise ValueError("synthetic rendering failure")
    try:
        layout.render(surface, failure, {"level": "group"})
    except ValueError as error:
        assert str(error) == "synthetic rendering failure"
    else:
        raise AssertionError("Rendering errors must propagate")
    assert not layout.active()
    assert len(layout.column_expressions(weights)) == len(weights)
    ast.parse(Path("analytical_layout.py").read_text())
    print("PASS: exact sizing-only patch, unchanged financial modules, scoped layout and error cleanup")


if __name__ == "__main__":
    main()
