# Exercise: the Sector Comparison Agent — solved

**Goal.** Add a fourth agent, a Sector Analyst, that compares the target stock
against its sector peers.

**Status.** Done. This document is the walkthrough: what was added, where, why it
sits where it does, and which parts of the old solution turned out to be wrong
rather than merely outdated.

Read it alongside `hints/`, which covers the same three steps at the level of
"what goes in the file". [README_CrewAI.md](README_CrewAI.md) describes the Lab 1
architecture this exercise runs on.

---

## Before you start: the shape of the problem changed

The previous solution to this exercise was ~60 lines of Python spread over three
functions in `streamlit_crewai_app.py`, plus a UI edit. On the current
architecture the crew is built from `src/stocks_crew/config/*.yaml`:

```python
# crew.py, build_crew() — the whole agent loop
for name, spec in _load_config("agents").items():
    ...
    agents[name] = Agent(role=spec["role"], goal=spec["goal"], ...)
```

`crew.py` does not know how many agents there are, and nothing is addressed by
index. So the exercise is now: **declare an agent, declare a task, wire the
report to it.** Three edits, two files, no Python.

That is the point of the exercise as much as the sector analysis is. Keep an eye
on how many of the old solution's steps were bookkeeping the framework should
have been doing for you.

---

## Step 1 — declare the agent

**File:** `src/stocks_crew/config/agents.yaml`
**Where:** a new top-level block, between `technical` and `report`.

```yaml
sector:
  role: Sector Comparison Specialist
  goal: >
    Place {symbol} against three to five peers traded in the same sector, using
    only quote and indicator values fetched for each peer.
  backstory: >
    You are a sector analyst who compares like with like. You propose peers from
    your own knowledge of the industry, but you confirm every one of them with
    `search_symbols` before you use it, and you fetch each peer's numbers with
    the tools -- you never recall a price or an RSI. You know that this desk's
    tools return quotes and technical indicators and nothing else, so you compare
    on those and say plainly that valuation and fundamentals are out of reach
    rather than estimating them.
  tools:
    - search_symbols
    - latest_quote
    - indicators
```

Three things worth pausing on:

**`{symbol}` is filled in for you.** `crew._render()` walks every string in the
block and substitutes `symbol`, `language`, `tone` and `horizon_days` from the
`run_analysis()` arguments. An unknown placeholder is left alone rather than
raising, so a typo shows up as a literal `{symbl}` in the prompt — worth knowing
when a prompt behaves strangely.

**The tool list is an allow-list, and it is short on purpose.** `price_series` is
not on it. A year of daily history for five peers is thousands of rows pushed
through the model's context, for a comparison `latest_quote` and `indicators`
already support. An allow-list is a budget as much as a boundary.

**The backstory does work the goal cannot.** "You never recall a price or an RSI"
and "say plainly that valuation and fundamentals are out of reach" are both there
to head off the failure in Step 4.

---

## Step 2 — declare the task

**File:** `src/stocks_crew/config/tasks.yaml`
**Where:** a new top-level block, after `technical` and before `report`. **This
placement is load-bearing** — see Step 3.

```yaml
sector:
  description: |
    Compare {symbol} against its sector peers, using the research note and the
    technical read as context.

    Pick three to five companies you believe trade in the same sector as
    {symbol}. That choice comes from your own knowledge -- no tool here returns a
    peer list -- which is exactly why each one must be confirmed before it is
    used:

      1. `search_symbols` for each candidate -- confirm the ticker exists and use
         the company name exactly as returned. Drop any candidate that does not
         resolve, and say which one you dropped.
      2. `latest_quote` for each confirmed peer -- price, change, change percent
         and volume.
      3. `indicators` for each confirmed peer -- last_close, SMA, EMA and RSI.

    Then compare {symbol} against them on those values only: ...

    Do NOT report P/E ratios, market capitalisation, revenue growth, analyst
    ratings or index membership. ...
  expected_output: >
    A sector comparison containing the confirmed peer tickers with the company
    names `search_symbols` returned, ... Every figure must come from a tool call.
  agent: sector
  context:
    - research
    - technical
```

(Trimmed to show the shape; the file has the full text.)

`agent: sector` is the whole of what used to be `tasks[2].agent =
agents["sector"]`. There is no index to get wrong.

`expected_output` is not decoration — CrewAI puts it in the prompt as the
acceptance criterion, which is why "every figure must come from a tool call"
belongs there. That is the thing being checked afterwards.

---

## Step 3 — wire the report to it

**File:** `src/stocks_crew/config/tasks.yaml`, the existing `report` block.

Two edits:

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
    - sector          # <-- without this, the sector agent's work is discarded
```

**This is the step that fails silently.** Skip it and everything still runs: the
sector agent is built, executes, calls its tools, and its output goes nowhere.
The trace fills with sector tool calls, the run costs more, and the report is
exactly what it was before. Nothing errors.

Two tests pin it, and they are the ones to look at if you are marking this:

```python
def test_the_report_task_is_given_the_sector_comparison():
    assert "sector" in crew._load_config("tasks")["report"]["context"]

def test_task_context_only_points_backwards():
    """Process.sequential runs tasks in YAML order, so context must already exist."""
```

The second is why block order matters. `context` may only name tasks that have
already run, so `sector` must sit after both of its inputs and before its
consumer. Move the block below `report` and that test fails immediately — the
good outcome, since the alternative is a task silently receiving nothing.

---

## Step 4 — the part the old solution got wrong

The previous version of this exercise shipped this task description, and the
hints reproduced it:

> Compare the target stock against these peers across multiple dimensions:
> 1. Price performance (1M, 3M, 6M, 1Y)
> 2. **Key financial metrics (P/E ratio, market cap, revenue growth)**
> 3. Technical indicators (RSI, moving averages, volatility)
> 4. Sector positioning and competitive advantages

Now list what this project's MCP server actually serves:

| Tool | Returns |
|---|---|
| `search_symbols` | symbol, name, region, currency |
| `latest_quote` | price, change, change percent, volume |
| `price_series` | OHLCV rows |
| `indicators` | last_close, SMA, EMA, RSI |
| `detect_events` | gap / volatility / 52-week flags |

There is no P/E ratio. No market cap. No revenue growth. No competitive
advantages. **Line 2 of that brief instructs the agent to report numbers it has
no way to obtain**, and item 4 asks for qualitative claims with no source at all.
The agent complies, the way a model does: it fills them in from whatever it
absorbed during training, in the same confident register as the figures it
genuinely fetched. A reader cannot tell the two apart.

On the old architecture nothing caught this. On the current one `grounding.py`
compares every figure in the report against the full output of every tool call,
and each invented metric surfaces as an `ungrounded_figure` in the UI panel.

So the brief was rewritten to ask only for what the tools return, and to close
the gap explicitly rather than leave it to the model's judgement:

> Do NOT report P/E ratios, market capitalisation, revenue growth, analyst
> ratings or index membership. No tool on this desk returns them, so any figure
> you write for them would be invented. State once that valuation and
> fundamental metrics are outside the available tools, and move on.

**An agent's job description is bounded by its tools.** A task that outruns them
is the most common way to manufacture a hallucination, and it is a design
mistake, not a model failure.
`tests/unit/test_stocks_crew/test_crew.py::test_the_sector_brief_forbids_metrics_no_tool_returns`
keeps the old brief from creeping back.

Worth doing in class: paste the old brief in, run once with the guardrail panel
open, and read the red list.

### The gap that remains

Fixing the metrics does not make the sector comparison fully grounded, and it is
worth being honest about why.

No tool returns a peer list, so the *choice* of peers is the model's own. The
brief requires each candidate to be confirmed with `search_symbols`, which proves
the ticker resolves to a real company — it does not prove the company is a peer.
Ask for peers of a bank, get an airline, and every number in the comparison will
be correctly fetched, correctly attributed, and grounded as far as
`grounding.py` can tell.

So: the peers' **numbers** are verified, their **relevance** is not. The
grounding check cannot see the difference, because it asks "where did this number
come from?" and never "is this the right number to be quoting?" Designing a check
for the second question is a genuinely open exercise, and a good one to hand to
the room.

---

## Step 5 — the Python that had to change anyway

None of this was required by the agent; all of it was a hardcoded count or name
the fourth agent exposed.

**`streamlit_crewai_app.py`** — a display label, and a count that was a literal:

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

The columns themselves already adapted: `st.columns(len(defaults))`, with
`defaults` coming from `crew.default_agent_tools()`, i.e. from the YAML. An agent
with no entry in `labels` still gets a column, labelled with its key — so
forgetting this step degrades gracefully instead of raising.

**`src/stocks_crew/crew.py`** — one progress line that named the agents:

```python
step(f"Crew assembled on {config.provider.label} ({config.model}) — running "
     + " → ".join(_load_config("tasks")) + "…", 30)
```

It used to read `"running research → technical → report…"`. Deriving it means the
next agent added to the YAML shows up here for free — the same principle as
`len(defaults)` above. A hardcoded list of things the config already knows is a
bug waiting for its second author.

---

## Verifying it

```bash
python -m pytest tests/unit/test_stocks_crew   # offline, no tokens
make lab1-tools
make lab1-ui
```

The offline suite covers the wiring. Under "The exercise" in
`tests/unit/test_stocks_crew/test_crew.py`:

| Test | What it pins |
|---|---|
| `test_the_crew_runs_four_agents_in_order` | `research → technical → sector → report` |
| `test_the_sector_agent_exists_and_can_reach_peer_data` | the agent, and its three tools |
| `test_the_sector_agent_cannot_pull_full_price_history` | `price_series` stays off the list |
| `test_the_sector_task_reads_both_earlier_analyses` | `context: [research, technical]` |
| `test_the_report_task_is_given_the_sector_comparison` | the silent-failure step |
| `test_the_sector_brief_forbids_metrics_no_tool_returns` | the P/E trap stays closed |

Every one of them is an assertion about YAML. That is the exercise in one line.

Then run it for real on a few symbols from different sectors — **AAPL**, **JPM**,
**JNJ**, **XOM** — and check three things in the UI each time:

1. The report has a sector section naming confirmed peers.
2. The trace shows `search_symbols` / `latest_quote` / `indicators` calls *per
   peer*, not just for the target.
3. The guardrail panel passes. If it does not, read which figure it names — that
   is the exercise working.

---

## Extensions worth assigning next

Roughly in order of how much they teach per hour spent:

- **Break it on purpose.** Restore the old P/E brief, run once, read the guardrail
  panel. Fastest way to make "bounded by its tools" concrete.
- **Take `search_symbols` away from the sector agent** in the sidebar and re-run.
  The brief says to drop unconfirmed peers. Does it?
- **Cap the peer count in the brief and watch the trace length.** Three tools per
  peer is a cost curve students can see rather than be told about.
- **Add a real `peer_metrics` MCP tool** — `tools.py`, an `@mcp.tool()` wrapper in
  `mcp_server.py`, `EXPECTED_TOOLS` in `tests/unit/test_stocks_crew/test_mcp_server.py`. This is the
  server-side half of the architecture, which this exercise never touches, and it
  turns the peer list from a model guess into tool output.
- **Make the guardrail block rather than report**, and decide what the UI shows
  when it does. Then decide whether it retries, and where that loop terminates.
- **A `context: [sector]` agent that only critiques** — a reviewer with no tools
  whose job is to flag claims the sector analyst cannot support. Cheap to add now
  (one more YAML block), and it raises the question of whether an LLM reviewer is
  a guardrail at all.
