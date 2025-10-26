# Task Creation Hint

## Task Structure
```python
task_name = Task(
    description="""Your task description""",
    expected_output="Expected output format",
    agent=None,  # Will be assigned later
    tools=[list_of_tools],
    context=[previous_tasks]  # Dependencies
)
```

## For Sector Task:

### Description
Should ask to identify sector peers and compare metrics. Example:
```python
description="""Using the company information from the research analysis and technical indicators from the technical analysis, 
identify 3-5 key sector peers for the target stock. Compare the target stock against these peers across multiple dimensions:
- Price performance (1M, 3M, 6M, 1Y)
- Key financial metrics (P/E ratio, market cap, revenue growth)
- Technical indicators (RSI, moving averages, volatility)
- Sector positioning and competitive advantages

Provide a comprehensive sector comparison that highlights where the target stock stands relative to its peers."""
```

### Expected Output
Be specific about the format. Example:
```python
expected_output="""A detailed sector comparison report including:
1. List of 3-5 identified sector peers with brief company descriptions
2. Comparative performance table showing key metrics for target stock vs peers
3. Analysis of relative strengths and weaknesses
4. Sector positioning summary with investment implications
5. Overall sector outlook and target stock's position within it"""
```

### Context
Should depend on `[research_task, technical_task]`:
```python
context=[research_task, technical_task]
```

### Tools
Can reuse existing MCP tools:
```python
tools=[search_tool, quote_tool, indicators_tool]
```

## Complete Example
```python
sector_task = Task(
    description="""Using the company information from the research analysis and technical indicators from the technical analysis, 
identify 3-5 key sector peers for the target stock. Compare the target stock against these peers across multiple dimensions:
- Price performance (1M, 3M, 6M, 1Y)
- Key financial metrics (P/E ratio, market cap, revenue growth)
- Technical indicators (RSI, moving averages, volatility)
- Sector positioning and competitive advantages

Provide a comprehensive sector comparison that highlights where the target stock stands relative to its peers.""",
    expected_output="""A detailed sector comparison report including:
1. List of 3-5 identified sector peers with brief company descriptions
2. Comparative performance table showing key metrics for target stock vs peers
3. Analysis of relative strengths and weaknesses
4. Sector positioning summary with investment implications
5. Overall sector outlook and target stock's position within it""",
    agent=None,
    tools=[search_tool, quote_tool, indicators_tool],
    context=[research_task, technical_task]
)
```

## Where to Add It
Add this code in the `create_tasks()` function, after the existing three tasks and before the return statement.

## Key Points
- The task should build on previous analysis (research and technical)
- Use the same tools as other tasks
- Make the expected output detailed and specific
- The context ensures proper task ordering
