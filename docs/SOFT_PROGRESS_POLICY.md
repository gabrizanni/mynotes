# Soft progress and loop recovery

Writing and managed correction account for elapsed time without stopping
productive work after a fixed twenty-minute interval. Explicit caller deadlines,
local validation limits, cancellation, schema checks and evidence requirements
still apply. Resuming preserves recorded consumption and checkpoints.

Worker prompts and incremental continuations instruct the agent to preserve
completed content, identify its last checkpoint, break repeated unproductive
actions and try another permitted approach. These reminders do not authorize
additional paid retries, model changes or weaker source validation.

Incremental Agy sessions record a reminder after five minutes without recognized
progress. They do not inject another model turn into an active stream-json
session. This is preventive guidance rather than guaranteed recovery from every
hang; buffered providers have no live progress inference. Existing interactive
coordinators must reload their managed instructions or start a new chat.

This policy does not change installed applications by itself. Runtime deployment
and managed integration updates remain separate operations from Git commits.
