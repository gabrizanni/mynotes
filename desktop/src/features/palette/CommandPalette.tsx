import { ArrowLeft, CalendarDays, Command as CommandIcon, FileAudio, FolderOpen, History, Mic, Moon, Settings, Square, Sun, type LucideIcon } from "lucide-react";
import { Fragment, useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { isMac, shortcutLabel } from "../../app/platform";
import { Button, Dialog, Icon, useNativeDialog } from "../../components";
import { cx } from "../../design/cx";
import { resolveTheme, systemPrefersDark } from "../../design/theme";
import { useI18n } from "../../i18n";
import { useActions, useAppState } from "../../state";
import { requestImport } from "../library/importRequest";
import { buildCommands, filterCommands, type Command, type PaletteAction, type PaletteGroup } from "./model";
import styles from "./Palette.module.css";

function iconFor(command: Command): LucideIcon {
  switch (command.action.kind) {
    case "record":
      return Mic;
    case "stop":
      return Square;
    case "import":
      return FileAudio;
    case "theme":
      return command.action.theme === "dark" ? Moon : Sun;
    case "pickUndo":
    case "undo":
      return History;
    case "navigate":
      return command.action.route.name === "settings" ? Settings : command.action.route.name === "course" ? FolderOpen : CalendarDays;
  }
}

/** ⌘/Ctrl K: one search field over every command; Esc closes it, Esc again closes the assistant. */
export function CommandPalette() {
  const { t, format } = useI18n();
  const actions = useActions();
  const courses = useAppState((state) => state.capture.dashboard?.courses ?? state.config.config?.courses ?? null);
  const recording = useAppState((state) => state.capture.dashboard?.running === true && state.capture.dashboard.runtime.recording === true);
  const theme = useAppState((state) => state.ui.theme);
  const assistantOpen = useAppState((state) => state.ui.assistantOpen);

  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"all" | "undo">("all");
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [confirm, setConfirm] = useState<{ course: string; name: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const listId = useId();
  const optionId = (index: number) => `${listId}-option-${index}`;

  const close = useCallback(() => {
    setOpen(false);
    returnFocus.current?.focus();
  }, []);
  const dialog = useNativeDialog(open, close);

  const show = useCallback(() => {
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setMode("all");
    setQuery("");
    setActive(0);
    setOpen(true);
  }, []);

  useEffect(() => {
    const mac = isMac();
    const onKey = (event: globalThis.KeyboardEvent) => {
      const mod = mac ? event.metaKey : event.ctrlKey;
      if (mod && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (open) close();
        else show();
        return;
      }
      // Esc closes the assistant dock when no dialog or menu holds the key.
      if (event.key === "Escape" && !event.defaultPrevented && !open && assistantOpen && !document.querySelector("dialog[open]")) {
        actions.ui.setAssistantOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, show, close, assistantOpen, actions]);

  useEffect(() => {
    if (open) input.current?.focus();
  }, [open, mode]);

  const commands = useMemo(
    () =>
      buildCommands({
        t,
        courses: courses ?? [],
        recording,
        resolvedTheme: resolveTheme(theme, systemPrefersDark()),
        shortcut: shortcutLabel,
        mode,
      }),
    [t, courses, recording, theme, mode],
  );
  const results = useMemo(() => filterCommands(commands, query), [commands, query]);
  const current = Math.min(active, Math.max(0, results.length - 1));

  const run = (action: PaletteAction) => {
    switch (action.kind) {
      case "pickUndo":
        setMode("undo");
        setQuery("");
        setActive(0);
        return;
      case "undo":
        setOpen(false);
        setConfirm({ course: action.course, name: action.name });
        return;
      case "navigate":
        setOpen(false);
        actions.ui.navigate(action.route);
        return;
      case "record": {
        setOpen(false);
        const stamp = new Date();
        void actions.capture.start({ unassigned: true, title: t.today.action.recordingTitle(`${format.dayShort(stamp)} ${format.time(stamp)}`), durationMinutes: 180 });
        return;
      }
      case "stop":
        setOpen(false);
        void actions.capture.stop().then(() => actions.ui.toast({ message: t.nav.recorderSaving, tone: "neutral" }));
        return;
      case "import":
        setOpen(false);
        requestImport();
        actions.ui.navigate({ name: "library", course: null, q: "", unassigned: false });
        return;
      case "theme":
        setOpen(false);
        actions.ui.setTheme(action.theme);
        return;
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive(results.length === 0 ? 0 : (current + step + results.length) % results.length);
    } else if (event.key === "Home" && results.length > 0) {
      event.preventDefault();
      setActive(0);
    } else if (event.key === "End" && results.length > 0) {
      event.preventDefault();
      setActive(results.length - 1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const command = results[current];
      if (command) run(command.action);
    } else if (event.key === "Backspace" && query === "" && mode === "undo") {
      event.preventDefault();
      setMode("all");
    }
  };

  const undo = async () => {
    if (!confirm) return;
    setBusy(true);
    const result = await actions.jobs.undoLastUpdate(confirm.course);
    setBusy(false);
    actions.ui.toast(result.ok ? { message: t.palette.undone(confirm.name), tone: "success" } : { message: t.palette.undoFailed, tone: "danger" });
    setConfirm(null);
  };

  const groupLabel = (group: PaletteGroup) => t.palette.groups[group];

  return (
    <>
      <dialog
        ref={dialog}
        className={styles.palette}
        aria-label={t.palette.title}
        onClick={(event) => {
          if (event.target === event.currentTarget) close();
        }}
      >
        {open && (
          <div className={styles.inner}>
            <div className={styles.searchRow}>
              {mode === "undo" ? (
                <button type="button" className={styles.back} aria-label={t.palette.back} onClick={() => setMode("all")}>
                  <Icon icon={ArrowLeft} size={20} />
                </button>
              ) : (
                <Icon icon={CommandIcon} size={20} className={styles.searchIcon} />
              )}
              <input
                ref={input}
                className={styles.input}
                type="text"
                role="combobox"
                aria-label={t.palette.search}
                aria-expanded="true"
                aria-controls={listId}
                aria-autocomplete="list"
                aria-activedescendant={results.length > 0 ? optionId(current) : undefined}
                placeholder={mode === "undo" ? t.palette.groups.undo : t.palette.placeholder}
                value={query}
                spellCheck={false}
                autoComplete="off"
                onChange={(event) => {
                  setQuery(event.target.value);
                  setActive(0);
                }}
                onKeyDown={onKeyDown}
              />
              <kbd className={styles.kbd}>Esc</kbd>
            </div>
            <ul id={listId} role="listbox" aria-label={t.palette.results} className={styles.list}>
              {results.map((command, index) => {
                const previous = results[index - 1];
                const heading = !query && (!previous || previous.group !== command.group);
                return (
                  <Fragment key={command.id}>
                    {heading && (
                      <li role="presentation" className={styles.groupLabel} aria-hidden="true">
                        {groupLabel(command.group)}
                      </li>
                    )}
                    <li
                      id={optionId(index)}
                      role="option"
                      aria-selected={index === current}
                      className={cx(styles.option, index === current && styles.active)}
                      onMouseMove={() => setActive(index)}
                      onClick={() => run(command.action)}
                    >
                      <Icon icon={iconFor(command)} size={16} className={styles.optionIcon} />
                      <span className={styles.optionLabel}>{command.label}</span>
                      {command.shortcut && <kbd className={styles.kbd}>{command.shortcut}</kbd>}
                    </li>
                  </Fragment>
                );
              })}
            </ul>
            {results.length === 0 && (
              <p className={styles.empty} role="status">
                {t.palette.empty}
              </p>
            )}
            <p className={styles.hint} aria-hidden="true">
              {t.palette.hint}
            </p>
          </div>
        )}
      </dialog>
      <Dialog
        open={confirm !== null}
        role="alertdialog"
        onClose={() => setConfirm(null)}
        title={confirm ? t.palette.undoTitle(confirm.name) : ""}
        description={t.palette.undoBody}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirm(null)}>
              {t.common.cancel}
            </Button>
            <Button variant="primary" loading={busy} onClick={() => void undo()}>
              {t.palette.undoConfirm}
            </Button>
          </>
        }
      />
    </>
  );
}
