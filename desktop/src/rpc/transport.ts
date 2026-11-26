/** A transport moves raw JSON-RPC calls and notifications; the client adds types and checks. */
export interface Transport {
  /** `stdio` is the Node transport of the contract tests (tests/contract/stdioTransport.ts). */
  readonly kind: "tauri" | "mock" | "stdio";
  request(method: string, params: unknown): Promise<unknown>;
  /** Listen to every server notification; returns an unsubscribe function. */
  subscribe(listener: (method: string, params: unknown) => void): () => void;
  close(): void;
}
