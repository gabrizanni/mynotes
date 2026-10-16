# Third-Party Software Notices and Licenses

MyNotes incorporates or interacts with the following open-source software libraries:

## 1. pypdfium2 (PDFium)
- **License:** Apache License 2.0 / BSD 3-Clause
- **Description:** Python bindings to PDFium, the open-source PDF rendering engine developed by Google and the Chromium project.
- **Project URL:** https://github.com/pypdfium2-team/pypdfium2

## 2. PyAV (FFmpeg)
- **License:** BSD 3-Clause (PyAV wrapper) / GNU Lesser General Public License (LGPL) v2.1+ (FFmpeg libraries)
- **Description:** Pythonic bindings for the FFmpeg multimedia framework, used for optional local audio clipping and format conversion fallback.
- **Project URL:** https://github.com/PyAV-Org/PyAV and https://ffmpeg.org

## 3. Pillow
- **License:** HPND License (Historical Permission Notice and Disclaimer) / PIL Software License
- **Description:** The friendly PIL fork for Python image processing.
- **Project URL:** https://github.com/python-pillow/Pillow

## 4. python-docx
- **License:** MIT License
- **Description:** Python library for reading and writing Microsoft Word (.docx) files.
- **Project URL:** https://github.com/python-openxml/python-docx

## 5. python-pptx
- **License:** MIT License
- **Description:** Python library for creating and updating PowerPoint (.pptx) presentations.
- **Project URL:** https://github.com/scanny/python-pptx

## Calendar runtime

- icalendar 7.3.0: BSD-2-Clause; https://github.com/collective/icalendar
- recurring-ical-events 3.8.2: LGPL-3.0-or-later, unmodified dynamically imported
  Python library. Source for this exact release:
  https://github.com/niccokunzmann/python-recurring-ical-events/archive/10792b9039e5ec1ba01ff3098af3beb705c1e649.zip
- x-wr-timezone 2.0.1: LGPL-3.0-or-later, unmodified dynamically imported Python
  library; https://github.com/niccokunzmann/x-wr-timezone

Dependency distributions retain their license files in the installed environment.
The user can replace or modify these Python libraries; they are not compiled into
the native recorder. MyNotes does not alter their license terms.

## Setup and local transcription runtime

- uv: MIT OR Apache-2.0. The packaged executable is unmodified; upstream notices
  must accompany it. [Source and licenses](https://github.com/astral-sh/uv).
- CPython: Python Software Foundation license and included component licenses.
  Managed distributions retain their license files. [CPython license](https://docs.python.org/3/license.html).
- Argmax OSS Swift / WhisperKit: MIT. The packaging machine builds a pinned
  upstream checkout, retaining its license and dependency notices beside the
  bundled command-line executable/resources. [Source and license](https://github.com/argmaxinc/argmax-oss-swift).
- Speech model files are downloaded from the configured pinned model repository;
  the setup retains its model metadata and manifest. The release must retain the
  original model/tokenizer notices as well as those for the executable.
  [WhisperKit models](https://huggingface.co/argmaxinc/whisperkit-coreml) and
  [OpenAI Whisper model](https://huggingface.co/openai/whisper-large-v3).
- LibreOffice: Mozilla Public License 2.0, with additional bundled component
  licenses. Setup downloads an unmodified official distribution and retains its
  built-in license information. [Licenses](https://www.libreoffice.org/licenses/)
  and [source downloads](https://www.libreoffice.org/download/download-libreoffice/).

Provider CLIs are external installations managed by their publishers. They are
not bundled into MyNotes, and their terms are separate from the MyNotes MIT
license. The release manifest records bundled file hashes; download manifests
record the exact selected runtime versions and integrity inputs.
