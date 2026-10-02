# Synthetic examples

`demo-course.json`, `demo-transcript.txt`, `demo-materials.txt`, and
`demo-personal-notes.txt` are original synthetic development fixtures.
They are safe to ship and do not contain course material, recordings, private
transcripts, or private output.

They do not activate a fallback provider through the production CLI. Automated
tests inject their own fake provider; normal processing remains blocked until
the production adapters are implemented.
