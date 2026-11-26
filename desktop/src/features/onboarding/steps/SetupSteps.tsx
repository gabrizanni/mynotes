import { AudioLines, CircleCheck, FileText, Image, LoaderCircle, Mic, NotebookPen, Presentation, Settings, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Banner, Card, ChoiceCards, Icon, Segmented, StatusChip } from "../../../components";
import { useT, type Lang } from "../../../i18n";
import { useActions, useAppState, useRpc } from "../../../state";
import { useOnboarding } from "../controller";
import { AUTO_ADVANCE_MS, LOGIN_POLL_MS, PROVIDERS, signInPhase, type Provider } from "../model";
import styles from "../Onboarding.module.css";
import { GUIDES } from "../platform";
import { StepLayout } from "../StepLayout";

function openGuide(url: string) {
  window.open(url, "_blank", "noopener,noreferrer");
}

/** 1 · Language: defaults to the OS language and switches the window at once. */
export function LanguageStep() {
  const t = useT();
  const { record, update, complete } = useOnboarding();
  const uiLang = useAppState((state) => state.ui.lang);
  const { ui } = useActions();
  const value: Lang = record.language ?? uiLang;
  const choose = (lang: Lang) => {
    ui.setLang(lang);
    update((current) => ({ ...current, language: lang }));
  };
  return (
    <StepLayout
      display
      title={t.onboarding.steps.language}
      lede={t.onboarding.intro}
      primary={{ label: t.onboarding.getStarted, onSelect: () => complete("language", (current) => ({ ...current, language: value })) }}
    >
      <Segmented
        label={t.onboarding.language.label}
        hint={t.onboarding.language.hint}
        options={[
          { value: "en", label: t.onboarding.language.options.en },
          { value: "it", label: t.onboarding.language.options.it },
        ]}
        value={value}
        onChange={choose}
      />
    </StepLayout>
  );
}

/** 2 · AI assistant: three cards; Continue starts the background install. */
export function AssistantStep() {
  const t = useT();
  const { record, update, complete, startSetup, setup } = useOnboarding();
  const a = t.onboarding.assistant;
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [explain, setExplain] = useState(false);
  const confirmed = useRef(record.marks.assistant === "done" ? record.provider : null);

  const onContinue = async () => {
    const provider = record.provider;
    if (!provider) {
      setError(a.required);
      return;
    }
    const needsStart = setup.phase === "idle" || setup.phase === "failed" || confirmed.current !== provider;
    if (needsStart) {
      setBusy(true);
      const started = await startSetup(provider);
      setBusy(false);
      if (!started) {
        setError(a.startFailed);
        return;
      }
    }
    complete("assistant");
  };

  return (
    <StepLayout
      title={t.onboarding.steps.assistant}
      lede={a.lede}
      width="wide"
      primary={{ label: t.common.continue, onSelect: () => void onContinue(), loading: busy }}
      secondary={[{ label: a.which, onSelect: () => setExplain((open) => !open) }]}
    >
      <ChoiceCards
        label={a.groupLabel}
        hideLabel
        options={PROVIDERS.map((provider) => ({ value: provider, title: a.providers[provider].name, description: a.providers[provider].account }))}
        value={record.provider}
        onChange={(provider: Provider) => {
          setError(null);
          update((current) => ({ ...current, provider }));
        }}
      />
      {error && <Banner tone="danger">{error}</Banner>}
      {explain && (
        <p className={styles.disclosure} role="note">
          {a.whichBody}
        </p>
      )}
      <p className={styles.note}>{a.startsSetup}</p>
    </StepLayout>
  );
}

/** 3 · Sign in: spawn the provider's login, poll every 3 s for 5 min, advance once ready. */
export function SignInStep() {
  const t = useT();
  const rpc = useRpc();
  const { record, complete, skip, providerName, now } = useOnboarding();
  const s = t.onboarding.signIn;
  const provider: Provider = record.provider ?? "claude";
  const [status, setStatus] = useState<string | null>(null);
  const [loginAt, setLoginAt] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const [checking, setChecking] = useState(false);
  const arrivedReady = useRef<boolean | null>(null);

  const check = useCallback(async () => {
    setChecking(true);
    try {
      const answer = await rpc.call("provider.status", { provider });
      setStatus(answer.status);
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setChecking(false);
    }
  }, [rpc, provider]);

  useEffect(() => {
    void check();
    return rpc.on("provider.authChanged", (event) => {
      if (event.provider === provider) setStatus(event.status);
    });
  }, [check, rpc, provider]);

  const phase = failed ? "error" : signInPhase(status, loginAt, now.getTime());
  if (arrivedReady.current === null && status !== null) arrivedReady.current = status === "ready";

  // While the browser sign-in runs, ask again every 3 s (UX-SPEC §2 step 3).
  useEffect(() => {
    if (phase !== "waiting") return undefined;
    const timer = setInterval(() => void check(), LOGIN_POLL_MS);
    return () => clearInterval(timer);
  }, [phase, check]);

  // Ready: a check mark, then on to the next step after 800 ms (only when it just happened).
  useEffect(() => {
    if (phase !== "ready" || arrivedReady.current === true) return undefined;
    const timer = setTimeout(() => complete("sign-in"), AUTO_ADVANCE_MS);
    return () => clearTimeout(timer);
  }, [phase, complete]);

  const login = async () => {
    try {
      await rpc.call("provider.login", { provider });
      setLoginAt(Date.now());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  };

  let primary: { label: string; onSelect: () => void; loading?: boolean } = { label: s.action, onSelect: () => void login() };
  let message: { tone: "info" | "warning" | "danger" | "success"; text: string; action?: { label: string; onSelect: () => void } } | null = null;
  switch (phase) {
    case "checking":
      primary = { label: s.action, onSelect: () => undefined, loading: true };
      break;
    case "missing":
      message = { tone: "danger", text: s.missing(providerName), action: { label: s.installGuide, onSelect: () => openGuide(GUIDES[provider]) } };
      primary = { label: s.checkAgain, onSelect: () => void check(), loading: checking };
      break;
    case "incompatible":
      message = { tone: "warning", text: s.incompatible(providerName), action: { label: s.updateGuide, onSelect: () => openGuide(GUIDES[provider]) } };
      primary = { label: s.checkAgain, onSelect: () => void check(), loading: checking };
      break;
    case "unverified":
      message = { tone: "info", text: s.unverified(providerName) };
      break;
    case "waiting":
      if (provider === "agy" || status === "unverified") {
        message = { tone: "info", text: s.unverified(providerName) };
        primary = { label: s.confirm, onSelect: () => complete("sign-in") };
      } else {
        primary = { label: s.checkAgain, onSelect: () => void check(), loading: checking };
      }
      break;
    case "timeout":
      message = { tone: "warning", text: s.timeout };
      primary = { label: s.retry, onSelect: () => void login() };
      break;
    case "error":
      message = { tone: "danger", text: s.failed };
      primary = { label: s.retry, onSelect: () => void check() };
      break;
    case "ready":
      primary = { label: t.common.continue, onSelect: () => complete("sign-in") };
      break;
    case "login_required":
      break;
  }

  return (
    <StepLayout
      title={s.title(providerName)}
      lede={s.lede}
      primary={primary}
      secondary={phase === "ready" ? [] : [{ label: s.later, onSelect: () => skip("sign-in") }]}
    >
      <Card padding="lg">
        <div className={styles.stack} aria-live="polite">
          <div className={styles.offRow}>
            <span className="type-heading">{providerName}</span>
            {status !== null && !failed && <StatusChip kind="assistant" status={status} />}
          </div>
          {phase === "checking" && (
            <p className={styles.liveLine}>
              <Icon icon={LoaderCircle} size={20} className={styles.spin} />
              {s.checking}
            </p>
          )}
          {phase === "waiting" && provider !== "agy" && (
            <p className={styles.liveLine}>
              <Icon icon={LoaderCircle} size={20} className={styles.spin} />
              {s.waiting}
            </p>
          )}
          {phase === "ready" && (
            <p className={styles.liveLine}>
              <Icon icon={CircleCheck} size={20} className={styles.success} />
              {s.ready(providerName)}
            </p>
          )}
          {message && (
            <Banner tone={message.tone} action={message.action}>
              {message.text}
            </Banner>
          )}
        </div>
      </Card>
    </StepLayout>
  );
}

/** 4 · Privacy: what stays here, what goes to the provider and when. */
export function PrivacyStep() {
  const t = useT();
  const { record, complete, providerName } = useOnboarding();
  const p = t.onboarding.privacy;
  const company = t.onboarding.assistant.providers[record.provider ?? "claude"].company;
  const localIcons = [Mic, AudioLines, NotebookPen, Settings];
  const sentIcons = [FileText, Presentation, Image];
  return (
    <StepLayout title={t.onboarding.steps.privacy} lede={p.lede} width="wide" primary={{ label: p.accept, onSelect: () => complete("privacy") }}>
      <div className={styles.columns}>
        <Card variant="emphasis" padding="lg" title={p.local.title}>
          <ul className={styles.factList} role="list">
            {p.local.items.map((item, index) => (
              <li key={item} className={styles.fact}>
                <Icon icon={localIcons[index] ?? Mic} size={20} />
                {item}
              </li>
            ))}
          </ul>
        </Card>
        <Card padding="lg" title={p.sent.title(providerName)}>
          <ul className={styles.factList} role="list">
            {p.sent.items.map((item, index) => (
              <li key={item} className={styles.fact}>
                <Icon icon={sentIcons[index] ?? FileText} size={20} />
                {item}
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <div className={styles.fineprint}>
        <p className={styles.note}>
          <Icon icon={ShieldCheck} size={16} />
          <span>
            {p.terms(company)} {p.noAccount}
          </span>
        </p>
      </div>
    </StepLayout>
  );
}
