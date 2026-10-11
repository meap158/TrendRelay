/** Select visible products without dropping hidden selections or exceeding the cap. */
export function toggleVisibleProducts(selected: ReadonlySet<string>, visible: readonly string[], limit: number): Set<string> {
  const next = new Set(selected);
  if (visible.length && visible.every((id) => next.has(id))) {
    visible.forEach((id) => next.delete(id));
  } else {
    visible.forEach((id) => { if (next.size < limit) next.add(id); });
  }
  return next;
}
