/**
 * Settings screen data that has no push topic: model preferences (`preferences --show`), model
 * catalogs (`models --json`), the setup check (`doctor --json`) and assistant sign-in status.
 * Read on demand when a settings section opens; every write goes through `attempt`.
 */
import { doctorReportSchema, providerIdSchema, type DoctorReport, type ModelCatalog, type ModelPreferences, type ModelSelection } from "../../rpc";
import { untilJobFinished } from "../../rpc/jobs";
import { failed, idle, loading, ready, type Load } from "../load";
import { attempt, type ActionResult } from "../result";
import type { SliceContext } from "../types";

export interface Loaded<T> {
  readonly load: Load;
  readonly value: T | null;
}

export interface ProviderState {
  readonly status: string;
  readonly version: string | null;
}

export interface SettingsState {
  readonly preferences: Loaded<ModelPreferences>;
  readonly catalogs: Readonly<Record<string, Loaded<ModelCatalog>>>;
  readonly doctor: Loaded<DoctorReport>;
  readonly providers: Readonly<Record<string, ProviderState>>;
}

const empty = { load: idle, value: null } as const;

export const settingsInitial: SettingsState = { preferences: empty, catalogs: {}, doctor: empty, providers: {} };

export interface ModelChoices {
  readonly default: ModelSelection;
  /** `null`: notes use the same assistant as everything else. */
  readonly notes: ModelSelection | null;
  readonly language: "en" | "it";
}

const pick = ({ provider, model, effort }: ModelSelection) => ({ provider, model, effort });

export function settingsActions(ctx: SliceContext<"settings">) {
  const loadPreferences = async () => {
    ctx.set((state) => ({ preferences: { ...state.preferences, load: state.preferences.value ? state.preferences.load : loading } }));
    try {
      const { data } = await ctx.rpc.call("preferences.get");
      ctx.set({ preferences: { load: ready, value: data } });
    } catch (error) {
      ctx.set((state) => ({ preferences: { ...state.preferences, load: failed(error) } }));
    }
  };

  const loadCatalog = async (provider: string, refresh = false) => {
    ctx.set((state) => {
      const current = state.catalogs[provider] ?? empty;
      return { catalogs: { ...state.catalogs, [provider]: { ...current, load: loading } } };
    });
    try {
      const saved = ctx.get().preferences.value?.default;
      const params = saved && saved.provider === provider ? { provider, saved: saved.model, savedEffort: saved.effort } : { provider };
      // `models.refresh` is a job that rewrites the cached list; `models.catalog` then reads it.
      if (refresh) await untilJobFinished(ctx.rpc, () => ctx.rpc.call("models.refresh", params), 60_000);
      const { data } = await ctx.rpc.call("models.catalog", params);
      ctx.set((state) => ({ catalogs: { ...state.catalogs, [provider]: { load: ready, value: data } } }));
    } catch (error) {
      ctx.set((state) => {
        const current = state.catalogs[provider] ?? empty;
        return { catalogs: { ...state.catalogs, [provider]: { ...current, load: failed(error) } } };
      });
    }
  };

  const loadDoctor = async (refresh = false) => {
    ctx.set((state) => ({ doctor: { ...state.doctor, load: loading } }));
    try {
      const { data } = await ctx.rpc.call("doctor.get", refresh ? { refresh: true } : {});
      // `doctor --json` checks carry an `action`; the shared schema only types `message`.
      ctx.set({ doctor: { load: ready, value: doctorReportSchema.parse(data) } });
    } catch (error) {
      ctx.set((state) => ({ doctor: { ...state.doctor, load: failed(error) } }));
    }
  };

  const checkProvider = async (provider: string): Promise<ActionResult<ProviderState>> => {
    const result = await attempt(async () => ctx.rpc.call("provider.status", { provider }));
    if (!result.ok) return result;
    const value: ProviderState = { status: result.value.status, version: result.value.version ?? null };
    ctx.set((state) => ({ providers: { ...state.providers, [provider]: value } }));
    return { ok: true, value };
  };

  /** Save the shared choice, the notes override and the language in one revisioned write. */
  const saveModels = (choices: ModelChoices) =>
    attempt(async () => {
      const current = ctx.get().preferences.value;
      if (!current) throw new Error("Preferences are not loaded yet");
      const modelsJson = {
        default: pick(choices.default),
        overrides: { chat: current.overrides.chat ? pick(current.overrides.chat) : null, notes: choices.notes ? pick(choices.notes) : null },
        language: choices.language,
      };
      const { data } = await ctx.rpc.call("preferences.saveModels", { models: modelsJson, expectedRevision: current.revision });
      ctx.set({
        preferences: {
          load: ready,
          value: { ...current, default: modelsJson.default, overrides: { ...current.overrides, notes: modelsJson.overrides.notes }, language: choices.language },
        },
      });
      return data;
    });

  return {
    loadPreferences,
    loadCatalog,
    loadDoctor,
    checkProvider,
    saveModels,
    saveLanguage: (language: "en" | "it") => {
      const current = ctx.get().preferences.value;
      if (!current) return Promise.resolve<ActionResult<undefined>>({ ok: true, value: undefined });
      return saveModels({ default: current.default, notes: current.overrides.notes ?? null, language }).then((result) =>
        result.ok ? { ok: true as const, value: undefined } : result,
      );
    },
    // The provider comes from saved preferences (a string): unknown ids fail here, not on the server.
    signIn: (provider: string) => attempt(async () => (await ctx.rpc.call("provider.login", { provider: providerIdSchema.parse(provider) })).jobId),
    saveProfile: (background: string) => attempt(async () => (await ctx.rpc.call("profile.save", { background })).data),
    verifyWord: () => attempt(async () => (await ctx.rpc.call("word.verifyRenderer")).jobId),
    installSpeechModel: () => attempt(async () => (await ctx.rpc.call("speech.installModel")).jobId),
    verifySpeech: (language: "en" | "it") => attempt(async () => (await ctx.rpc.call("speech.verify", { language })).jobId),
  };
}
