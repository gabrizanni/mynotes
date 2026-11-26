import type { RpcClient } from "../rpc";
import type { AssistantState } from "./slices/assistant";
import type { CaptureState } from "./slices/capture";
import type { ConfigState } from "./slices/config";
import type { JobsState } from "./slices/jobs";
import type { LibraryState } from "./slices/library";
import type { QueueState } from "./slices/queue";
import type { SettingsState } from "./slices/settings";
import type { UiState } from "./slices/ui";

/** The whole app state: one slice per server topic, plus jobs, assistant and UI. */
export interface AppState {
  readonly capture: CaptureState;
  readonly queue: QueueState;
  readonly library: LibraryState;
  readonly config: ConfigState;
  readonly jobs: JobsState;
  readonly assistant: AssistantState;
  readonly ui: UiState;
  /** Settings screen data read on demand (workspace screens). */
  readonly settings: SettingsState;
}

export type SliceKey = keyof AppState;

/** What a slice's actions receive: its own state, a patch setter, the RPC client and a root read. */
export interface SliceContext<K extends SliceKey> {
  get(): AppState[K];
  set(patch: Partial<AppState[K]> | ((state: AppState[K]) => Partial<AppState[K]>)): void;
  rpc: RpcClient;
  root(): AppState;
}
