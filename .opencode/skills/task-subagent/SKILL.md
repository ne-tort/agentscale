---
name: task-subagent
description: How to call the Task tool (subagents) correctly in this opencode build. Valid subagent_type values, common mistakes, parallel invocation. Use whenever you are about to call the Task tool or are debugging an Unknown agent type error.
---

# Task tool (subagents) - canon for this opencode build

The subagent_type parameter in a Task call is the agent name, not a model. Do not confuse it with the model field of an agent config (opencode.json or markdown frontmatter) - that is a separate concept and is NOT passed to the Task tool.

## Valid subagent_type values (verified empirically in this build)

| subagent_type | works | mode | when to use |
|---|---|---|---|
| general | yes | subagent | multi-step tasks, file edits, parallelism |
| explore | yes | subagent (read-only) | find files/code in the codebase |
| build | yes | primary | full tool access (when a subagent is not enough) |
| plan | yes | primary | analysis without edits |
| scout | NO | - | Unknown agent type in this build, do not use |

## Invalid values (produce Unknown agent type)

- inherit - this is an agent CONFIG field (model: inherit), not a subagent_type.
- auto, Auto - not agent names.
- General, Explore, Build, Plan with a capital letter - case matters, only lowercase works.
- empty string - error.
- any invented name like some-nonexistent-agent - error.

## Rules for calling Task

- subagent_type is required, exactly one of the values above, lowercase.
- description is required, short (1-5 words).
- prompt is required, a concrete task with a done-criterion.
- In parallel: several task calls in one message, when tasks are independent.
- task_id only to resume an existing session.
