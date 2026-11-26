import { KeyRound, RefreshCw, RotateCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Banner, Button, Card, Segmented, Select, StatusChip, Toggle } from "../../components";
import { useT } from "../../i18n";
import type { ModelCatalog, ModelSelection } from "../../rpc";
import { useActions, useAppState } from "../../state";
import { chosenModel } from "../../state/slices/settings";
import styles from "./Settings.module.css";

export const PROVIDERS = ["claude", "codex", "agy"] as const;

interface Choice {
  provider: string;
  model: string;
  effort: string;
}

interface Draft {
  main: Choice;
  separate: boolean;
  notes: Choice;
}

const pick = (selection: ModelSelection | null | undefined, fallback: Choice): Choice =>
  selection ? { provider: selection.provider, model: selection.model, effort: selection.effort } : fallback;

function sameChoice(a: Choice, b: Choice): boolean {
  return a.provider === b.provider && a.model === b.model && a.effort === b.effort;
}

/** One assistant + model + thinking depth picker (used twice when notes use another one). */
function ChoiceFields({ value, onChange, catalog, idPrefix }: { value: Choice; onChange: (next: Choice) => void; catalog: ModelCatalog | null; idPrefix: string }) {
  const t = useT();
  const a = t.settings.assistant;
  const choices = catalog?.choices ?? [];
  const options = choices.map((choice) => ({
    value: choice.model,
    label: choice.availability === "provider_default" ? a.modelDefault : choice.availability === "saved_unavailable" ? a.modelUnavailable(choice.label) : choice.label,
    disabled: !choice.selectable,
  }));
  if (!options.some((option) => option.value === value.model)) {
    options.unshift({ value: value.model, label: value.model === "" ? a.modelDefault : value.model, disabled: false });
  }
  const current = choices.find((choice) => choice.model === value.model);
  const efforts = current?.supported_efforts ?? [];
  return (
    <div className={styles.fields}>
      <div className={styles.fieldRow}>
        <Select
          id={`${idPrefix}-provider`}
          label={a.provider}
          value={value.provider}
          hint={a.account(a.companies[value.provider] ?? value.provider)}
          options={PROVIDERS.map((provider) => ({ value: provider, label: a.providers[provider] ?? provider }))}
          onChange={(event) => onChange({ provider: event.target.value, model: "", effort: "" })}
        />
        <Select
          id={`${idPrefix}-model`}
          label={a.model}
          value={value.model}
          options={options}
          onChange={(event) => {
            const next = choices.find((choice) => choice.model === event.target.value);
            onChange({ ...value, model: event.target.value, effort: next?.default_effort ?? "" });
          }}
        />
      </div>
      {efforts.length > 0 && (
        <Segmented
          label={a.depth}
          hint={a.depthHint}
          value={efforts.includes(value.effort) ? value.effort : (current?.default_effort ?? efforts[0] ?? "")}
          options={efforts.map((effort) => ({ value: effort, label: a.efforts[effort] ?? effort.charAt(0).toUpperCase() + effort.slice(1) }))}
          onChange={(effort) => onChange({ ...value, effort })}
        />
      )}
    </div>
  );
}

/** Settings › AI assistant: who writes the notes, with which model and how deeply. */
export function AssistantSection() {
  const t = useT();
  const a = t.settings.assistant;
  const actions = useActions();
  const preferences = useAppState((state) => state.settings.preferences);
  const catalogs = useAppState((state) => state.settings.catalogs);
  const providers = useAppState((state) => state.settings.providers);
  const lang = useAppState((state) => state.ui.lang);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState<"save" | "check" | "signin" | null>(null);

  useEffect(() => {
    void actions.settings.loadPreferences();
  }, [actions]);

  const saved = useMemo<Draft | null>(() => {
    const value = preferences.value;
    if (!value) return null;
    const main = pick(chosenModel(value.default), { provider: "claude", model: "", effort: "" });
    return { main, separate: Boolean(value.overrides.notes), notes: pick(value.overrides.notes, main) };
  }, [preferences.value]);

  useEffect(() => {
    if (saved) setDraft(saved);
  }, [saved]);

  const mainProvider = draft?.main.provider ?? null;
  const notesProvider = draft?.separate ? draft.notes.provider : null;
  useEffect(() => {
    for (const provider of [mainProvider, notesProvider]) {
      if (!provider) continue;
      void actions.settings.loadCatalog(provider);
      void actions.settings.checkProvider(provider);
    }
  }, [actions, mainProvider, notesProvider]);

  if (!draft || !saved) {
    return preferences.load.status === "error" ? (
      <Banner tone="danger" action={{ label: t.common.retry, onSelect: () => void actions.settings.loadPreferences() }}>
        {t.settings.loadError}
      </Banner>
    ) : (
      <div className={styles.skeleton} aria-busy="true" aria-label={t.common.loading} />
    );
  }

  const dirty = !sameChoice(draft.main, saved.main) || draft.separate !== saved.separate || (draft.separate && !sameChoice(draft.notes, saved.notes));
  const mainCatalog = catalogs[draft.main.provider];
  const status = providers[draft.main.provider]?.status ?? null;
  const providerName = a.providers[draft.main.provider] ?? draft.main.provider;

  const check = async () => {
    setBusy("check");
    const result = await actions.settings.checkProvider(draft.main.provider);
    setBusy(null);
    if (!result.ok) actions.ui.toast({ message: a.statusFailed, tone: "danger" });
  };
  const signIn = async () => {
    setBusy("signin");
    const result = await actions.settings.signIn(draft.main.provider);
    setBusy(null);
    actions.ui.toast(result.ok ? { message: a.signingIn, tone: "neutral" } : { message: a.statusFailed, tone: "danger" });
  };
  const save = async () => {
    setBusy("save");
    const result = await actions.settings.saveModels({ default: { ...draft.main }, notes: draft.separate ? { ...draft.notes } : null, language: lang });
    setBusy(null);
    actions.ui.toast(result.ok ? { message: t.settings.saved, tone: "success" } : { message: t.settings.saveFailed, tone: "danger" });
  };

  const catalogNote =
    mainCatalog?.load.status === "error" ? a.modelsFailed : mainCatalog?.value?.status === "ready" ? a.modelsFresh : mainCatalog?.value ? a.modelsStale : null;

  return (
    <div className={styles.stack}>
      <Card>
        <div className={styles.cardStack}>
          <div className={styles.statusLine}>
            <span className={styles.statusLabel}>{a.status}</span>
            {status ? <StatusChip kind="assistant" status={status} /> : <StatusChip kind="assistant" status="unverified" />}
            {providers[draft.main.provider]?.version && <span className={styles.muted}>{providers[draft.main.provider]?.version}</span>}
            <span className={styles.spacer} />
            {status === "login_required" ? (
              <Button size="sm" variant="soft" icon={KeyRound} loading={busy === "signin"} onClick={() => void signIn()}>
                {a.signIn}
              </Button>
            ) : (
              <Button size="sm" variant="ghost" icon={RotateCw} loading={busy === "check"} onClick={() => void check()}>
                {a.checkAgain}
              </Button>
            )}
          </div>
          <ChoiceFields idPrefix="main" value={draft.main} catalog={mainCatalog?.value ?? null} onChange={(main) => setDraft({ ...draft, main })} />
          <div className={styles.inlineNote}>
            {catalogNote && <span className={styles.muted}>{catalogNote}</span>}
            <Button
              size="sm"
              variant="ghost"
              icon={RefreshCw}
              loading={mainCatalog?.load.status === "loading" && mainCatalog.value !== null}
              onClick={() => void actions.settings.loadCatalog(draft.main.provider, true)}
            >
              {a.refreshModels}
            </Button>
          </div>
        </div>
      </Card>

      <Card>
        <div className={styles.cardStack}>
          <Toggle label={a.separateNotes} description={a.separateNotesBody} checked={draft.separate} onChange={(separate) => setDraft({ ...draft, separate })} />
          {draft.separate && <ChoiceFields idPrefix="notes" value={draft.notes} catalog={catalogs[draft.notes.provider]?.value ?? null} onChange={(notes) => setDraft({ ...draft, notes })} />}
        </div>
      </Card>

      <div className={styles.footer}>
        <p className={styles.muted}>{a.privacy(draft.separate ? (a.providers[draft.notes.provider] ?? draft.notes.provider) : providerName)}</p>
        <Button variant="primary" disabled={!dirty} loading={busy === "save"} onClick={() => void save()}>
          {t.settings.save}
        </Button>
      </div>
    </div>
  );
}
