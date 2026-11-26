/**
 * "Import audio" from Calendar's Add menu or the command palette opens the Library's import
 * step. The request survives the route change in memory only; Library reads it once.
 */
let pending = false;

export function requestImport(): void {
  pending = true;
}

export function consumeImportRequest(): boolean {
  const value = pending;
  pending = false;
  return value;
}
