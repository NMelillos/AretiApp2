// Read-only browser assertion. Run against expanded report_visual_qa.py at each
// supported viewport, passing its recorded pre-change rendered measurement.
function verifyAnalyticalSizing(baseline) {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const close = (a, b, label) => assert(Math.abs(a - b) <= 1, `${label}: ${a} != ${b}`);
  const visible = e => e.getBoundingClientRect().width > 0;
  const button = label => [...document.querySelectorAll('button')].find(e => visible(e) && e.textContent.trim() === label);
  const group = button(baseline.label || 'Woking Way LLC');
  const category = button('Tour Income') || button('Synthetic International Development and Administration Income');
  const subcategory = button('Todd Regan');
  assert(group && category && subcategory, 'All three expanded hierarchy levels are required');
  const [g, c, s] = [group, category, subcategory].map(e => e.getBoundingClientRect());
  close(g.width, Math.max(200, baseline.button.width * 2), 'Double original group with authorised word-fit minimum');
  close(c.width, g.width * .75, 'Category 3/4');
  close(s.width, g.width * .5, 'Subcategory 2/4');
  assert(g.left < c.left && c.left < s.left, 'Increasing indentation');
  close(g.right, c.right, 'Group/category right edge');
  close(g.right, s.right, 'Group/subcategory right edge');
  let row = group.closest('[data-testid="stHorizontalBlock"]');
  while (!row.querySelector('.drill-cell')) row = row.parentElement.closest('[data-testid="stHorizontalBlock"]');
  const cells = [...row.querySelectorAll('.drill-cell')];
  assert(cells.length === baseline.cells.length, 'Monetary column count');
  cells.forEach((cell, i) => {
    const r = cell.getBoundingClientRect(), old = baseline.cells[i];
    assert(cell.textContent === old.text, `Column ${i} value/order`);
    close(r.width, old.rect.width, `Column ${i} width`);
    close(r.x - old.rect.x, g.width - baseline.button.width, `Column ${i} block shift`);
    assert(r.left >= g.right, `Column ${i} must not overlap group`);
  });
  const scope = group.closest('[class*="st-key-analytical_report_"]');
  assert(scope.scrollWidth > scope.clientWidth, 'Horizontal scrolling is required');
  assert(scope.scrollHeight <= scope.clientHeight + 1, 'No vertical clipping inside scroll area');
  const total = [...scope.querySelectorAll('.drill-total-label')].find(visible);
  assert(total && total.textContent === 'TOTAL', 'TOTAL row remains available');
  const totalCells = [...total.closest('[data-testid="stHorizontalBlock"]').querySelectorAll('.drill-cell')].slice(1);
  totalCells.forEach((cell, i) => close(cell.getBoundingClientRect().x, cells[i].getBoundingClientRect().x, `TOTAL ${i} alignment`));
  const header = [...scope.querySelectorAll('[class*="st-key-analytical_row_"]')][0];
  [...header.querySelectorAll('.summary-label')].slice(1).forEach((cell, i) => close(cell.getBoundingClientRect().x, cells[i].getBoundingClientRect().x, `Header ${i} alignment`));
  for (const branch of scope.querySelectorAll('[class*="st-key-executive_hierarchy_branch_"]')) {
    const css = getComputedStyle(branch, '::before');
    assert(css.width === '3px' && css.backgroundColor !== 'rgba(0, 0, 0, 0)', 'Visible green connector');
  }
  for (const control of [group, category, subcategory]) {
    assert(control.scrollWidth <= control.clientWidth + 1, 'Label horizontal clipping');
    assert(control.scrollHeight <= control.clientHeight + 1, 'Label vertical clipping');
  }
  const hierarchy = [...scope.querySelectorAll('[class*="st-key-executive_group_"] button, [class*="st-key-executive_category_"] button, [class*="st-key-executive_subcategory_"] button')].filter(visible);
  for (const control of hierarchy) {
    const paragraph = control.querySelector('p');
    const text = [...paragraph.childNodes].find(n => n.nodeType === 3);
    assert(text, 'Hierarchy label text exists');
    const bounds = paragraph.getBoundingClientRect();
    for (const word of text.textContent.matchAll(/[A-Za-z]+/g)) {
      const range = document.createRange();
      range.setStart(text, word.index);
      range.setEnd(text, word.index + word[0].length);
      const fragments = [...range.getClientRects()];
      assert(fragments.length === 1, `Mid-word break: ${word[0]}`);
      assert(fragments[0].left >= bounds.left - 1 && fragments[0].right <= bounds.right + 1, `Clipped word: ${word[0]}`);
    }
    assert(control.getBoundingClientRect().height <= Math.max(22, bounds.height + 6), 'Excess empty label height');
  }
  const ai = [...row.querySelectorAll('button')].find(e => visible(e) && e.textContent.trim() === 'AI');
  if (baseline.ai.length) {
    assert(ai, 'AI remains present');
    const r = ai.getBoundingClientRect();
    close(r.width, baseline.ai[0].width, 'AI width');
    assert(r.left >= g.right && r.right <= cells[0].getBoundingClientRect().left, 'AI separation');
  }
  return { group: g.width, category: c.width, subcategory: s.width, monetaryColumns: cells.length, passed: true };
}
