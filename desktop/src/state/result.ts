/**
 * Outcome of a user action that calls the backend. Actions catch their own errors and return
 * this, so screens can show "problem + one fix" without try/catch in components.
 */
export type ActionResult<T = undefined> = { readonly ok: true; readonly value: T } | { readonly ok: false; readonly error: string };

export function succeed(): ActionResult;
export function succeed<T>(value: T): ActionResult<T>;
export function succeed<T>(value?: T): ActionResult<T | undefined> {
  return { ok: true, value };
}

export function fail(error: unknown): { readonly ok: false; readonly error: string } {
  return { ok: false, error: error instanceof Error ? error.message : String(error) };
}

/** Run one backend call and turn a throw into `{ ok: false }`. */
export async function attempt<T>(run: () => Promise<T>): Promise<ActionResult<T>> {
  try {
    return succeed(await run());
  } catch (error) {
    return fail(error);
  }
}
