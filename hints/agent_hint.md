# Hint 1 — creating the agent

You are not writing Python. `crew.py` builds an `Agent` for every top-level block
in `src/stocks_crew/config/agents.yaml`, so adding an agent means adding a block.

## The shape of a block

```yaml
short_name:            # the key tasks.yaml refers to
  role: >
    ...
  goal: >
    ...
  backstory: >
    ...
  tools:               # MCP tool names this agent is allowed to call
    - tool_name
```

`role`, `goal` and `backstory` go straight to `crewai.Agent`. `tools` is this
project's own extension: an allow-list, applied when the crew is built. Omit it
or leave it empty and the agent gets no tools at all (that is how the report
writer is configured).

## For the sector agent

**Key:** `sector`

**Where:** between the `technical` and `report` blocks. Block order here sets the
column order in the Streamlit UI; the order that actually *runs* is set in
`tasks.yaml`.

**Role**

```yaml
  role: Sector Comparison Specialist
```

**Goal** — say what it produces, and from what:

```yaml
  goal: >
    Place {symbol} against three to five peers traded in the same sector, using
    only quote and indicator values fetched for each peer.
```

**Backstory** — this is where behaviour goes, not biography. The old version of
this hint suggested "a seasoned sector analyst with 15 years of experience",
which reads well and instructs the model in nothing. Prefer sentences that rule
out a specific failure:

```yaml
  backstory: >
    You are a sector analyst who compares like with like. You propose peers from
    your own knowledge of the industry, but you confirm every one of them with
    `search_symbols` before you use it, and you fetch each peer's numbers with
    the tools -- you never recall a price or an RSI. You know that this desk's
    tools return quotes and technical indicators and nothing else, so you compare
    on those and say plainly that valuation and fundamentals are out of reach
    rather than estimating them.
```

Every clause there is load-bearing:

| Clause | Failure it heads off |
|---|---|
| "confirm every one with `search_symbols`" | comparing against a ticker that does not exist |
| "never recall a price or an RSI" | quoting remembered figures instead of calling the tool |
| "quotes and indicators and nothing else" | inventing P/E ratios — see `hints/task_hint.md` |

**Tools**

```yaml
  tools:
    - search_symbols
    - latest_quote
    - indicators
```

Note what is missing. `price_series` would let this agent pull a year of daily
history for each of five peers — thousands of rows through the model's context,
for a comparison the two tools above already support. An allow-list is a budget
as much as a boundary.

The names must match what the MCP server advertises. Check the live list rather
than trusting this file:

```bash
make lab1-tools
```

A name that does not match is not an error — `crew._select_tools()` logs a
warning and hands the agent one fewer tool. Which is worth knowing, because the
symptom is an agent that quietly stops calling something.

## Placeholders

`{symbol}`, `{language}`, `{tone}` and `{horizon_days}` are substituted at build
time from the `run_analysis()` arguments, anywhere in the block. Unknown
placeholders are left alone rather than raising, so `{symbl}` reaches the model
as literal text — check here first when a prompt behaves oddly.

## Check it

```bash
pytest tests/unit/test_stocks_crew/test_crew.py -q
```

Relevant tests: `test_the_sector_agent_exists_and_can_reach_peer_data`,
`test_the_sector_agent_cannot_pull_full_price_history`, and
`test_agent_tool_names_match_the_tool_layer` (which catches a mistyped tool
name).

Next: `hints/task_hint.md`.
