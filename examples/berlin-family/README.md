# Berlin family events (seed)

Starting point for a real collection repo that gathers family-friendly events in Berlin. It
holds the schema and the research brief only: sources are found and set up by the assistant
following `CLAUDE.md` in the project repo, because that needs live access to the sites.

Start a repo from it:

```bash
gleanyard init berlin-family-events --schema family-event
cp examples/berlin-family/schema.yaml examples/berlin-family/research.md berlin-family-events/
cd berlin-family-events && gleanyard validate
```

Then ask the assistant to run the research procedure from `research.md`.
