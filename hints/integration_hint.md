# Hint 3 — integration

Most of what the old version of this hint covered no longer exists. It listed
five steps; four of them were keeping two Python lists in sync by index, and the
framework does that now. What is left is one real step and two cosmetic ones.

## The one that matters

**The report task has to be told the sector output exists.**

`src/stocks_crew/config/tasks.yaml`, in the existing `report` block:

```yaml
report:
  description: |
    Write the final report on {symbol}, synthesizing the research note, the
    technical read and the sector comparison. ...

    Sections (translate these headings into {language}):
      ...
      4. Sector comparison (the confirmed peers and where {symbol} sits among
         them). Carry the peer figures through as the sector analyst reported
         them, and repeat the note that valuation and fundamental metrics were
         not available from the tools. Do not rank the peers as investments.
      ...
  context:
    - research
    - technical
    - sector          # <-- the line
```

Both halves are needed. `context` makes the output available; the new section in
`description` makes the writer use it. Add the first without the second and the
sector analysis reaches the writer, which quietly decides it has enough material
already.

### Why this is the dangerous step

Skip it entirely and **nothing errors**. The crew builds, the sector agent runs,
its tools get called, the trace fills with sector calls, the run takes longer and
costs more — and the report is identical to what it was before. There is no
exception, no warning, no empty section. The only visible symptom is a bill.

That is the shape of most agent-pipeline bugs: not a crash, but work that
happens and is then discarded.

```python
# tests/unit/test_stocks_crew/test_crew.py
def test_the_report_task_is_given_the_sector_comparison():
    # Without this the sector agent runs, spends tokens, and its output never
    # reaches the report — the quietest way for this exercise to "work".
    assert "sector" in crew._load_config("tasks")["report"]["context"]
```

## The cosmetic ones

None of these are required for the agent to work. Each is a hardcoded number that
a fourth agent exposed.

**1. `streamlit_crewai_app.py` — a label and a count**

```python
labels = {
    "research": "🔍 Research Agent",
    "technical": "📈 Technical Analyst",
    "sector":    "🏢 Sector Analyst",     # added
    "report":    "📝 Report Writer",
}
...
f"<p>Runs step {list(defaults).index(agent_name) + 1} of {len(defaults)}.</p>"
#                                                        ^^^^^^^^^^^^^^^^ was "3"
```

The columns already adapted on their own — `st.columns(len(defaults))`, where
`defaults` comes from the YAML. An agent with no `labels` entry still gets a
column, labelled with its key, so skipping this is ugly rather than broken.

**2. `src/stocks_crew/crew.py` — a progress line**

```python
step(f"Crew assembled on {config.provider.label} ({config.model}) — running "
     + " → ".join(_load_config("tasks")) + "…", 30)
```

It used to spell out `"research → technical → report"`. Deriving it from the
config means the next agent appears here for free.

## What you no longer have to do

Straight from the old version of this file, for comparison:

| Old step | Now |
|---|---|
| add `"sector": sector_agent` to the dict `create_agents()` returns | — the YAML *is* the dict |
| add `sector_task` to the list `create_tasks()` returns | — file order *is* the list |
| `tasks[3].agent = agents["sector"]` | `agent: sector` in the task block |
| renumber the assignments that followed | — nothing is indexed |
| `col1, col2, col3, col4 = st.columns(4)` | — `st.columns(len(defaults))` |

The old hint's "Common Integration Mistakes" section listed *"Wrong task index —
make sure `tasks[3]` corresponds to the 4th task (0-indexed)"*. That bug can no
longer be written.

## Checklist

- [ ] `sector` block in `agents.yaml`, between `technical` and `report`
- [ ] `sector` block in `tasks.yaml`, after `technical` and before `report`
- [ ] `agent: sector` on the task
- [ ] `context: [research, technical]` on the task
- [ ] **`- sector` added to the report task's `context`**
- [ ] a sector section added to the report task's `description`
- [ ] `pytest` passes

## Verify

```bash
pytest -q                                # 111 tests, offline, no tokens
make lab1-tools  # the MCP handshake still works
make lab1-ui    # four agent cards
```

Then run one real analysis and check the trace shows `search_symbols` /
`latest_quote` / `indicators` calls **per peer**, not just for the target. If the
trace is short, the sector agent decided it already knew the answer — which is
the finding the trace exists to surface.
