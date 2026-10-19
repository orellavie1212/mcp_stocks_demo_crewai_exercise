# Hint 2 — creating the task

Same idea as the agent: a block in `src/stocks_crew/config/tasks.yaml`, not
Python.

## The shape of a block

```yaml
short_name:
  description: |         # what the agent is told to do
    ...
  expected_output: >     # the acceptance criterion, also part of the prompt
    ...
  agent: agent_key       # a key from agents.yaml
  context:               # keys of EARLIER tasks whose output is fed in
    - other_task
```

Two rules that have no equivalent in the Python version:

- **File order is run order.** `Process.sequential` runs the tasks in the order
  they appear in this file.
- **`context` may only name tasks that appear above.** They have to have run
  already. `test_task_context_only_points_backwards` enforces it.

So the `sector` block goes **after `technical` and before `report`**.

## Writing the description

Start from the tools the agent has, not from what a sector analyst does. This is
the whole lesson of the exercise — see [the P/E trap](#the-trap-worth-knowing-about)
below.

```yaml
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

    Then compare {symbol} against them on those values only: where its change
    percent sits in the group, where its RSI sits, how it stands relative to its
    own SMA and EMA compared with how the peers stand relative to theirs.

    Do NOT report P/E ratios, market capitalisation, revenue growth, analyst
    ratings or index membership. No tool on this desk returns them, so any figure
    you write for them would be invented. State once that valuation and
    fundamental metrics are outside the available tools, and move on.

    If a peer's tools return an error or no data, name the peer and the tool and
    exclude it from the comparison rather than filling the gap.

    Rules: no investment advice, no price targets, no predictions, and no ranking
    of peers as better or worse investments.
```

Points of technique:

- **Name the tools, in order.** The other task briefs do the same. It is a
  prompt, not a guarantee — which is exactly why the trace exists.
- **Say what to do when a tool fails.** Without it the model's instinct is to
  paper over the gap, and a gap papered over is indistinguishable from data.
- **Rule things out explicitly.** "Do NOT report P/E ratios…" is more reliable
  than hoping the absence of a tool is self-evident to the model. It is not.

## Writing expected_output

CrewAI puts this in the prompt as the acceptance criterion, so it is not
documentation — it is the second half of the instruction.

```yaml
  expected_output: >
    A sector comparison containing the confirmed peer tickers with the company
    names `search_symbols` returned, a table of {symbol} against each peer
    showing price, change percent and RSI as the tools returned them, a short
    read on where {symbol} sits in that group, an explicit note that valuation
    and fundamental metrics were not available from the tools, and a list of any
    peers dropped for missing data. Every figure must come from a tool call.
```

"Every figure must come from a tool call" belongs here because that is the thing
`grounding.py` checks afterwards. State the standard you are going to measure.

## Agent and context

```yaml
  agent: sector
  context:
    - research
    - technical
```

`agent: sector` replaces the entire `tasks[2].agent = agents["sector"]` dance
from the old version. There is no index to get wrong.

`context` gives this task the earlier outputs so it does not re-fetch the
target's own quote and indicators — those are already in the research note and
the technical read.

## The trap worth knowing about

The previous version of this exercise asked the sector task for:

> Key financial metrics (P/E ratio, market cap, revenue growth)

No tool in this project returns any of those. Asking for them instructs the agent
to make them up — and `grounding.py` now compares every figure in the report
against the full output of every tool call, so they come back flagged as
`ungrounded_figures`.

**An agent's job description is bounded by its tools.** If you want fundamentals
in the comparison, the fix is a new MCP tool in `tools.py` and `mcp_server.py`,
not a bolder prompt.

`test_the_sector_brief_forbids_metrics_no_tool_returns` keeps the old brief from
creeping back.

## Check it

```bash
pytest tests/unit/test_stocks_crew/test_crew.py -q
```

Relevant tests: `test_the_sector_task_reads_both_earlier_analyses`,
`test_the_crew_runs_four_agents_in_order`, `test_every_task_names_an_agent_that_exists`,
`test_task_context_only_points_backwards`.

Next: `hints/integration_hint.md` — the step that fails silently.
