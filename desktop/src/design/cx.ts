/** Join class names, skipping falsy values (CSS module lookups can be undefined). */
export function cx(...parts: ReadonlyArray<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
