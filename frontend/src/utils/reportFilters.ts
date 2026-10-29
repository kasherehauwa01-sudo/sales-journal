/** Keep old string-only report APIs compatible with empty/single selections.
 * Multiple selections stay arrays: never silently discard selected values.
 */
export function serializeReportSelection(values: readonly string[]): string | string[] | null {
  if (!values.length) return null;
  return values.length === 1 ? values[0] : [...values];
}
