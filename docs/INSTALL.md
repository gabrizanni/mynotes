# Install MyNotes + Capture

The DMG installer targets **Apple Silicon, macOS 14+**. It includes Capture. The current preview is available from GitHub Releases; standalone ZIP archives use the manual installer described below.

## From DMG to chat

1. Download the intended DMG from [GitHub Releases](https://github.com/gabrizanni/mynotes/releases).
2. Open it, drag **MyNotes** into **Applications**, and open **MyNotes** from there.
3. Follow the component setup, choose your agent, and select **Open chat**.

Python, locked Python packages, local speech tools and the Word renderer are prepared in MyNotes' managed storage. No Python, Homebrew, Xcode or agent inherited from another project is needed. Setup needs internet for component downloads and shows progress. Closing the app preserves completed steps; retry after correcting a download or disk-space problem.

MyNotes installs its launcher in `~/.local/bin` and adds a marked PATH block to the user’s zsh configuration. Open a new Terminal window before using `mynotes` from another folder.

## Open Anyway

This candidate has an ad-hoc signature, with no Apple Developer account or notarization. If macOS blocks the app, try opening it once, then go to **System Settings → Privacy & Security → Open Anyway** and confirm **Open** for MyNotes. This grants an exception for that app. [Apple's guide](https://support.apple.com/102445).

![Open Anyway schematic](images/open-anyway.svg)

The illustration is a schematic, not a captured screen. Labels depend on system language and macOS version. A managed Mac may restrict the exception; follow its administrator's policy. A missing button or a damaged-app message is not evidence that the installed candidate passed Gatekeeper: record the exact message and reinstall a verified candidate rather than disabling system protections.

## Choose an AI agent

MyNotes needs the selected **command-line agent**, not just its desktop app.

**Claude Code:** the Free account does not include this service, including with Haiku. Use an eligible plan or API access with credits. CLI installation and version/help checks do not require a paid generation. A sign-in check does not verify balance or model entitlement. [Official plans](https://claude.com/pricing), [API credits](https://support.claude.com/en/articles/8977456-how-do-i-pay-for-my-claude-api-usage).

| Agent | Official installation and sign-in help | After installation |
| --- | --- | --- |
| Codex | [Codex CLI](https://developers.openai.com/codex/cli/) | Sign in through Codex, then select **Check again** |
| Agy | [Antigravity CLI](https://antigravity.google/docs/cli/) | Complete its sign-in flow, then select **Check again** |
| Claude Code | [Claude Code setup](https://code.claude.com/docs/en/setup) | Sign in through Claude Code, then select **Check again** |

Setup invites you to install a missing agent; it does not install providers, create accounts or collect passwords. A detected launcher is separate from confirmed account access. An inconclusive authentication check asks you to sign in/open the agent, then retry. Unsupported versions show an update action. Provider defaults are used unless you explicitly choose overrides; MyNotes does not silently switch provider.

[First conversation and Capture](ONBOARDING.md) · [Privacy](PRIVACY.md)

## Repair and diagnostics

Reopen MyNotes or run `mynotes setup`. Run `mynotes doctor --json` for separate installation, agent, transcription, Word and Capture results and next actions. Capture can remain disabled while notes from existing recordings work. If microphone access was denied, enable **MyNotes Capture** in **System Settings → Privacy & Security → Microphone**, then repeat the short recording and listening test.

For an interrupted update, retry setup; the previous runtime remains selected until the replacement passes its checks. [Update recovery](UPDATES.md).

## Connect a course folder

After the first course is configured, give its folder or Finder alias to the
agent and ask it to connect that course. The agent can use one bounded link
command and its result; this task needs no global disk search or repeated
help/diagnostic exploration. Existing files and aliases stay in place.

```sh
mynotes course link COURSE "PATH" --json
```

Replace COURSE with your configured code and PATH with the folder/alias path;
dragging it into Terminal helps enter spaces correctly. MyNotes checks metadata
in that folder and immediate children, with a 1,000-entry limit. File previews list up to 50 paths per role, with
counts for the entries actually inspected; a shortened preview is separate
from an incomplete inventory. Clear notes and
recordings folders are connected; ambiguous or truncated results are shown
without saving. Select them explicitly with `--notes "NOTES_PATH"` and
`--recordings "AUDIO_PATH"`, or choose a more specific course folder.
`--dry-run` previews the result. The folder contents are not moved or read by
this inventory; local Finder alias resolution opens no dialog and mounts no
volume. Restore a broken alias target or choose the actual folder.

For a lesson's recorded provider attempts, ask the agent for usage or run
`mynotes usage COURSE-L01 --json`. Missing counters mean unknown, not zero;
failed attempts and local reuse are shown separately. This read-only report
makes no AI call and does not report an invoice or account balance. These
features passed isolated installed-wheel checks in 0.2.4. The next 0.2.5
candidate adds standalone Word-note handling; its package verification is
separate. [Validation](VALIDATION.md) · [Optimization evidence](OPTIMIZATION.md).

## Ordinary Word notes and a fresh MyNotes setup

In the 0.2.5 source, a selected standalone Word note file does not need special
lecture headings. Its text and tables are included for the lesson; a cumulative
notebook with recognized lesson headings still supplies the requested section.
The package will be checked separately before calling this installed behavior
verified. Imported note instructions remain source evidence; save a preference
explicitly through MyNotes when you want it to act as an instruction.

For a fresh-start trial on an existing account, use a reversible backup and
MyNotes-only reset/reinstall. Keep course originals, other apps, provider CLI
installations and their credentials. Record completed setup/course/local-tool
checks after the trial. Existing agents can remain installed: this tests fresh
MyNotes state, not a clean macOS account. The authorized trial being prepared
has not yet been reported as completed. [Validation details](VALIDATION.md).

## Manual ZIP installer (advanced / older releases)

An extracted standalone ZIP can use `zsh scripts/install-mynotes.sh`. This older/manual path requires macOS, Python 3.12+ and `uv`; it does not provide the DMG's component onboarding. Options include `--prefix PATH`, `--app-home PATH`, `--python PATH`, `--offline`, `--dry-run`, `--list-versions`, and `--rollback VERSION`. Offline mode needs already cached locked packages. A source checkout instead uses `uv sync --locked`; it is not a standalone release archive.
