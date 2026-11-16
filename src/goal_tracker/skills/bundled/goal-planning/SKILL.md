---
name: goal-planning
description: Help define or revise measurable weekly and monthly goals. Use when planning outcomes and checklist milestones before user review.
---

# Goal Planning

## Required context

- Selected weekly or monthly period and its dates.
- The user's goal description and expected outcome.
- Existing goals when supplied by the application.

## Instructions

1. Turn the requested outcome into a small, practical checklist.
2. Make each milestone a specific result that can be marked done.
3. Keep milestones distinct and avoid repeating existing work.
4. Match the scope to the selected period.
5. Do not invent deadlines, available time, skill levels or prior achievements.
6. Leave optional information unknown when it was not supplied.
7. Treat proposed goals and milestones as drafts.
8. Require user review before creating or revising stored records.
9. Preserve the original plan when suggesting later scope changes.

## Output

Return only the structured format requested by the application.

When proposing milestones, use concise descriptions with observable
completion criteria. Avoid vague outcomes such as "understand everything."

Example:

User objective: Learn the basics of LangChain.

Possible milestones:
- Complete the introductory tutorial.
- Build a working local Ollama integration.
- Run and explain one structured-output example.

These are proposals, not completed activities.

## Relevant MCP tools

- list_goals
- create_goal
- update_goal
- finalize_plan

The Python workflow controls tool execution and confirmation.
These instructions do not grant tool permissions.

## Failure behavior

If model assistance is unavailable, preserve manual goal creation.
Never save an incomplete or unreviewed proposal automatically.
