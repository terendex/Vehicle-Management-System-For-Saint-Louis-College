/* Vehicles Inside: everyone inside the campus, as GET /scan/inside/ returns it.
   Shared by the guard's entry page panel and the admin's Operations Center
   table, so the two always list the same people the same way.

   Each item is one of:
   - an entry inside (`row`), with the visitor pass it came in on (`pass`)
     when it is a visitor;
   - a pass still open with no entry inside (`row` null, `notInside` true):
     its slip never printed, so the entry was never logged. Listed so it is
     not lost, but not counted as inside. */

// The guard page holds its own, fresher copy of today's passes (it refetches
// them the moment one is extended), so a pass is read from there when it can be.
export function activeOwnerItems(inside, freshPasses = []) {
  const byId = new Map(freshPasses.map(p => [p.id, p]))
  const fresh = (p) => (p ? byId.get(p.id) ?? p : null)
  if (!inside) {
    // The inside list has not loaded (or failed): show the passes alone, as
    // the panel did before, rather than nothing.
    return freshPasses.map(p => ({ key: `pass-${p.id}`, group: 'visitor', row: null, pass: p, notInside: false }))
  }
  // Visitors on a pass first: they are on a clock and need watching. Within
  // each half the server's order (newest entry first) is kept.
  return [
    ...inside.results.map(r => ({ key: `in-${r.id}`, group: r.group, row: r, pass: fresh(r.pass), notInside: false })),
    ...(inside.passes_not_inside ?? []).map(p => ({
      key: `pass-${p.id}`, group: 'visitor', row: null, pass: fresh(p), notInside: true,
    })),
  ].sort((a, b) => (a.pass ? 0 : 1) - (b.pass ? 0 : 1))
}

// "All" plus one chip per group with someone in it. Supplier and event
// vehicles ('other') have no chip and show under All only. Counts are of the
// vehicles inside; a pass not inside yet is not one of them.
export function activeOwnerChips(items, groups = []) {
  const counted = items.filter(i => !i.notInside)
  const counts = counted.reduce((c, i) => ({ ...c, [i.group]: (c[i.group] || 0) + 1 }), {})
  return [{ key: 'all', label: 'All', count: counted.length },
    ...groups.filter(g => counts[g.key] || (g.key === 'visitor' && items.some(i => i.notInside)))
      .map(g => ({ ...g, count: counts[g.key] || 0 }))]
}

const squash = (s) => (s || '').toLowerCase().replace(/\s+/g, '')

// The chip and the plate-or-name search together. A chip that no longer has
// anyone in it falls back to All rather than showing an empty list.
export function filterActiveOwners(items, chips, group, query) {
  const g = chips.some(c => c.key === group) ? group : 'all'
  const q = squash(query)
  return {
    group: g,
    rows: items.filter(i =>
      (g === 'all' || i.group === g)
      && (!q || squash(`${i.row?.plate ?? i.pass?.plate_number ?? ''} ${i.row?.name || i.pass?.visitor_name || ''}`).includes(q))),
  }
}
