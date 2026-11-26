/** A transport moves raw JSON-RPC calls and notifications; the client adds types and checks. */
export interface Transport {
  readonly kind: "tauri" | "mock";
  request(method: string, params: unknown): Promise<unknown>;
  /** Listen to every server notification; returns an unsubscribe function. */
  subscribe(listener: (method: string, params: unknown) => void): () => void;
  close(): void;
}
