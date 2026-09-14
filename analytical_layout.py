"""Scoped analytical label widening without redistributing monetary columns."""
from contextvars import ContextVar

_layout = ContextVar("analytical_layout", default=None)


def active():
    return _layout.get() is not None


def render(st, function, arguments):
    key = arguments.get("selection_key") or "executive_" + arguments["level"]
    scope = "analytical_report_" + key
    state = {"scope": scope, "index": 0, "ready": False}
    with st.container(key=scope):
        token = _layout.set(state)
        try:
            return function(**arguments)
        finally:
            _layout.reset(token)


def column_expressions(widths):
    # Streamlit's equal flex growth distributes the 2px row gaps equally.
    gap_share = 2 * (len(widths) - 1) / len(widths)
    return [f"calc({100 * width / sum(widths):.12f}cqw - {gap_share:.12f}px)" for width in widths]


def columns(st, widths, *, ai=False, full=True):
    state = _layout.get()
    if state is None:
        return st.columns(widths)
    scope = f'.st-key-{state["scope"]}'
    expressions = column_expressions(widths)
    if not state["ready"]:
        first = expressions[0]
        label = f"calc(0.8 * {first} - 8px)" if ai else first
        state["label"] = label
        state["ready"] = True
        st.markdown(f"""<style>
        /* Reserve the horizontal scrollbar's clearance, not inter-row spacing. */
        {scope} {{ container-type: inline-size; overflow-x: auto; max-width: {'none' if full else '1120px'}; padding-bottom: 18px; }}
        {scope} [data-testid="stElementContainer"]:has(style) {{ display: none; }}
        {scope} [class*="st-key-executive_group_"] {{ width: calc(2 * {label}) !important; }}
        {scope} [class*="st-key-executive_category_"],
        {scope} [class*="st-key-executive_income_charity_category_"] {{
            width: calc(1.5 * {label}) !important; position: relative; left: calc(0.5 * {label}); align-self: flex-start !important;
        }}
        {scope} [class*="st-key-executive_subcategory_"],
        {scope} [class*="st-key-executive_income_charity_subcategory_"] {{
            width: {label} !important; position: relative; left: {label}; align-self: flex-start !important;
        }}
        {scope} [class*="st-key-executive_hierarchy_branch_"]::before,
        {scope} [class*="st-key-income_charity_branch_"]::before {{ left: 0; }}
        {scope} [class*="st-key-executive_group_"] button p,
        {scope} [class*="st-key-executive_category_"] button p,
        {scope} [class*="st-key-executive_subcategory_"] button p,
        {scope} [class*="st-key-executive_income_charity_"] button p {{ overflow-wrap: anywhere; }}
        </style>""", unsafe_allow_html=True)
    label = state["label"]
    key = f'analytical_row_{state["scope"]}_{state["index"]}'
    state["index"] += 1
    selector = f'.st-key-{key}'
    template = " ".join([f"calc({expressions[0]} + {label})"] + expressions[1:])
    st.markdown(f"""<style>
    {selector} > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] {{
        display: grid !important; grid-template-columns: {template};
        width: max-content !important; max-width: none !important; column-gap: 2px !important;
    }}
    {selector} > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{ width: auto !important; min-width: 0 !important; }}
    {selector} > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child [data-testid="stHorizontalBlock"] {{
        display: grid !important; grid-template-columns: calc(2 * {label}) calc({expressions[0]} - {label} - 16px);
        column-gap: 16px !important;
    }}
    {selector} > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
        width: auto !important; min-width: 0 !important;
    }}
    </style>""", unsafe_allow_html=True)
    with st.container(key=key):
        return st.columns(widths)
