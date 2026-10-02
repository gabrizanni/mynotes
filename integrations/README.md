# Integrations

Use `mynotes integrate codex --destination PROJECT` to create a repository-local
Codex skill. Use `mynotes integrate agy --destination PROJECT` to create a
workspace-local Antigravity plugin containing its manifest, skill, and primary
agent. Both exports are generic and deliberately contain no private workspace
path, course name, credential, or note.

Platform installation and selector validation remain a separate user-facing
step; see the release documentation before claiming an integration is live.
