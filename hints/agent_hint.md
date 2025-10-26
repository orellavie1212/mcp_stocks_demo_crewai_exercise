# Agent Creation Hint

## Agent Structure
```python
agent_name = Agent(
    role="Your Role Here",
    goal="Your Goal Here", 
    backstory="""Your backstory here""",
    tools=[list_of_tools],
    verbose=True,
    allow_delegation=False
)
```

## For Sector Agent:

### Role
Use: `"Sector Comparison Specialist"`

### Goal
Should mention comparing stocks against sector peers. Example:
```python
goal="Compare the target stock's performance, valuation, and fundamentals against its sector peers to identify relative strengths and weaknesses"
```

### Backstory
Write a backstory that explains the agent's expertise in sector analysis. Example:
```python
backstory="""You are a seasoned sector analyst with 15 years of experience in comparative market analysis. 
You specialize in identifying sector trends, peer comparisons, and relative performance metrics. 
Your expertise lies in understanding how individual stocks perform within their sector context 
and identifying which companies are sector leaders or laggards."""
```

### Tools
Use the same tools as other agents:
```python
tools=[search_tool, quote_tool, indicators_tool]
```

## Complete Example
```python
sector_agent = Agent(
    role="Sector Comparison Specialist",
    goal="Compare the target stock's performance, valuation, and fundamentals against its sector peers to identify relative strengths and weaknesses",
    backstory="""You are a seasoned sector analyst with 15 years of experience in comparative market analysis. 
You specialize in identifying sector trends, peer comparisons, and relative performance metrics. 
Your expertise lies in understanding how individual stocks perform within their sector context 
and identifying which companies are sector leaders or laggards.""",
    tools=[search_tool, quote_tool, indicators_tool],
    verbose=True,
    allow_delegation=False
)
```

## Where to Add It
Add this code in the `create_agents()` function, after the existing three agents and before the return statement.
