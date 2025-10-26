# Integration Hint

## Steps to Integrate:

### 1. Add to agents dictionary
In the `create_agents()` function, modify the return statement:

**Before:**
```python
return {
    "research": research_agent,
    "technical": technical_agent, 
    "report": report_agent
}
```

**After:**
```python
return {
    "research": research_agent,
    "technical": technical_agent, 
    "report": report_agent,
    "sector": sector_agent  # Add this line
}
```

### 2. Add to tasks list
In the `create_tasks()` function, modify the return statement:

**Before:**
```python
return [research_task, technical_task, report_task]
```

**After:**
```python
return [research_task, technical_task, report_task, sector_task]
```

### 3. Assign agent to task
In the `create_crew()` function, add the agent assignment:

**Look for this section:**
```python
# Assign agents to tasks
tasks[0].agent = agents["research"]
tasks[1].agent = agents["technical"]
tasks[2].agent = agents["report"]
```

**Add this line after the existing assignments:**
```python
# Assign agents to tasks
tasks[0].agent = agents["research"]
tasks[1].agent = agents["technical"]
tasks[2].agent = agents["report"]
tasks[3].agent = agents["sector"]  # Add this line
```

## Complete Integration Checklist

- [ ] Created `sector_agent` in `create_agents()` function
- [ ] Added `sector_agent` to the agents dictionary return
- [ ] Created `sector_task` in `create_tasks()` function  
- [ ] Added `sector_task` to the tasks list return
- [ ] Assigned `sector_agent` to `sector_task` in `create_crew()` function

## Common Integration Mistakes

1. **Forgetting to add agent to dictionary** - The agent won't be accessible
2. **Forgetting to add task to list** - The task won't be executed
3. **Wrong task index** - Make sure `tasks[3]` corresponds to the 4th task (0-indexed)
4. **Missing agent assignment** - The task won't know which agent to use

## Testing Your Integration

After making these changes:
1. Save the file
2. Restart the Streamlit app
3. Test with a stock symbol
4. Check that you see sector analysis in the output

## Expected Workflow Order

With proper integration, the tasks should execute in this order:
1. **Research Task** (Research Agent)
2. **Technical Task** (Technical Agent) 
3. **Sector Task** (Sector Agent) - depends on research and technical
4. **Report Task** (Report Agent) - depends on all previous tasks

The sector task will have access to the research and technical analysis results through its context dependencies.
