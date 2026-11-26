# Linux x86-64 runtime

**In breve (italiano).** MyNotes funziona su Linux x86-64 dalla riga di
comando: trascrizione locale con whisper.cpp sulla CPU (modello
`large-v3-turbo`, lo stesso di Windows, verificato con SHA-256), Word con il
LibreOffice di sistema, e Capture come servizio utente systemd che registra le
lezioni del calendario anche a interfaccia chiusa, con PipeWire (`pw-record`)
o, in alternativa, `ffmpeg -f pulse`. I dati privati restano in
`~/.local/share/mynotes`. Non c'è ancora un'app grafica per Linux. La tabella
qui sotto distingue ciò che è stato provato su una macchina reale da ciò che
non lo è ancora.

## What runs on Linux

| Part | Linux behavior |
| --- | --- |
| Runtime data | `~/.local/share/mynotes` (`MYNOTES_HOME` overrides it), the same root as the default workspace, as on macOS. Capture state lives in `<home>/Capture`. |
| New courses | `whispercpp` with `large-v3-turbo`, no diarization. macOS and Windows defaults are unchanged. |
| Speech engine | `whisper-cli` from whisper.cpp tag `b5454` (the tag the Windows build pins), built for the CPU. Lookup order: the course's `transcription.tools.whispercpp`, the managed tool in `tools.json`, then `PATH`. |
| Speech model | `mynotes install-speech-model` downloads the pinned `ggml-large-v3-turbo.bin` (1.62 GB, SHA-256 checked) into `<home>/speech-models/whisper.cpp`. |
| Verification | `mynotes verify-transcription --audio SAMPLE` (Linux has no built-in speech synthesizer, so a spoken sample is required, for example whisper.cpp's public `samples/jfk.wav`). `mynotes verify-word-renderer`. `mynotes doctor --json`. |
| Audio import | PyAV decodes and resamples; `afconvert` and `afinfo` are not used. |
| Recording | `mynotes.capture_linux`, a Python port of the Windows worker. It reads and writes the same request, response, session and `runtime.json` files. Audio is 48 kHz mono PCM staged in WAV segments that rotate every 10 minutes, then exported to M4A. Recordings and staging folders are owner-only (0600 and 0700). |
| Background service | `mynotes capture enable` writes `~/.config/systemd/user/mynotes-capture.service` (`Restart=on-failure`, `RestartSec=30`, `WantedBy=default.target`) and starts it; `mynotes capture disable` stops and removes it. |
| Suspend | A jump between `CLOCK_BOOTTIME` and `CLOCK_MONOTONIC` marks a suspend; the attempt is interrupted and resumes as a partial recording, as on Windows. |
| Notifications | `notify-send` at recording start and stop, only when `MYNOTES_CAPTURE_NOTIFY=1`. The service sets it. |

Optional recorder settings for the service environment:
`MYNOTES_CAPTURE_SOURCE` (a PipeWire target or PulseAudio source name; the
default input otherwise) and `MYNOTES_CAPTURE_RECORDER` (`pw-record` or
`ffmpeg`).

## Install from a source checkout

```sh
uv sync --locked
git clone --depth 1 --branch b5454 https://github.com/ggml-org/whisper.cpp.git
cmake -S whisper.cpp -B whisper.cpp/build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF \
  -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF -DWHISPER_SDL2=OFF -DWHISPER_CURL=OFF
cmake --build whisper.cpp/build --target whisper-cli --parallel 4
export PATH="$PWD/whisper.cpp/build/bin:$PATH"
uv run mynotes install-speech-model
uv run mynotes verify-transcription --audio whisper.cpp/samples/jfk.wav
uv run mynotes verify-word-renderer
uv run mynotes doctor --json
```

Install LibreOffice Writer with the distribution package manager, for example
`sudo pacman -S libreoffice-fresh` or `sudo apt install libreoffice-writer
libreoffice-math`.

## Install from the Linux payload

`scripts/build_linux_payload.py` builds `dist/MyNotes-<version>-linux-x86_64.tar.gz`
from `setup/components.linux.lock.json`: the wheel, the public sources, the
pinned uv, `whisper-cli` built with CMake from the pinned commit, the public
speech sample and a SHA-256 manifest of every file. Model weights are not
packaged.

```sh
tar -xzf MyNotes-<version>-linux-x86_64.tar.gz
python3.12 mynotes-<version>-linux-x86_64/scripts/setup_runtime.py \
  --release-root mynotes-<version>-linux-x86_64 --provider claude
```

Setup checks every payload file, downloads the pinned model, installs the
locked dependencies with `uv venv` and `uv pip install --require-hashes`,
registers `whisper-cli`, verifies transcription on the bundled sample and Word
rendering, and writes the `~/.local/bin/mynotes` launcher. It never edits
shell profiles; add `~/.local/bin` to `PATH` if your distribution does not.
If LibreOffice is missing it stops with a package-manager hint and rolls back.

## Parity with macOS

Reference build is macOS v0.4.0, with the rows of
[windows/PARITY.md](../windows/PARITY.md). "Proved" means a command run on one
Arch Linux x86-64 machine (AMD Ryzen 5 5600H, 6 cores and 12 threads,
PipeWire 1.6.11, LibreOffice 26.8.2.2, Python 3.12 and 3.13) during this
change. Audio was played into a temporary PipeWire null sink; no microphone
was recorded. "Not proved" rows still need a run.

| macOS screen or flow | Shared command or state contract | Linux status |
| --- | --- | --- |
| Setup and first launch | `init`, `doctor --json`, `preferences`, private workspace | Proved: the built payload installed with `setup_runtime.py` in 27 s (model pre-seeded, so its download was not exercised there), then `doctor --json` reported `ready: true` for installation, agent, transcription and Word. No graphical setup exists on Linux. |
| Library and courses | `capture dashboard`, `transcription-queue lessons`, `course` | Partly proved: `course add` created a course with the Linux transcription defaults, and the full test suite passes on Linux. Interactive use is not proved (no Linux app). |
| Lesson detail | `transcription-queue lesson-edit` and library actions | Not proved beyond the test suite. |
| Calendar | `capture import`, `subscribe`, `sync`, `source`, `schedule.json` | Partly proved: `capture import` of a timetable, then the worker recorded the scheduled lesson by itself. A live calendar feed and the five-minute `sync --if-due` are not proved. |
| Capture | Request, response, runtime, session and audio sidecars | Proved with `pw-record`: `capture start --unassigned --duration-minutes 1` produced an M4A, `capture test`, `capture stop` and `confirm-test` returned `VERIFIED`, and doctor then marked Capture ready; a scheduled lesson was recorded from `recordAt` to `stopAt` (180.1 s, `COMPLETED`). Proved with the FFmpeg fallback: a manual recording saved as `PARTIAL`. The manual, scheduled and FFmpeg recordings each transcribed back to the played sample. `capture enable` started the systemd service and `capture disable` removed it. Not proved: a real suspend and resume, a lost device, ten-minute rotation on real audio and desktop notifications (all covered by tests with a fake recorder). |
| Activity | `transcription-queue status` and job actions | Not proved beyond the test suite. |
| Offline transcription | ASR engine registry and normalized transcript format | Proved: `install-speech-model` (51 s), `verify-transcription --audio jfk.wav` with `large-v3-turbo` (11.0 s of audio in 19.8 s at the default 4 threads), PyAV normalization of recorded M4A. Measured `whisper-cli` speed: on a 120 s Italian LibriVox excerpt, 71.7 s with 4 threads (0.60 × real time) and 55.0 s with 8 threads (0.46 ×); on the 11 s JFK clip, 19.2 s and 11.5 s. Other jobs shared the CPU (load average 3 to 7). Long lessons, speaker attribution and cache reuse are not proved. |
| External chat | `chat --provider`, workspace rules, provider preferences | Not proved. The doctor probe found a signed-in Claude Code; no chat or paid call was made. |
| Notes and Word | Preparation, notes, publication and undo commands | Partly proved: `verify-word-renderer` rendered the equation test document with the system LibreOffice. Note generation with a provider is not proved. |
| Distribution | Release archive and SHA-256 | Partly proved: the payload builds and installs on this machine, and its SHA-256 file verifies. Upgrade, rollback and a clean second distribution are not proved; the CI job has not run yet. |

## Known limits

- MyNotes does not pass a thread count, so whisper.cpp uses its default of 4.
  On this machine 8 threads took 23% less time on the 120 s excerpt.
- The service runs the Python of the version that enabled it. After an update,
  run `mynotes capture enable` again to point it at the new version.
- FluidAudio recovery passes and diarization are not available on Linux.
- There is no tray icon or Linux desktop app yet; the notification is the only
  recording indicator while the CLI is closed.
