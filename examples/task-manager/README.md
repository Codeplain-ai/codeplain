# Two-module task manager example

This example is split into two modules to demonstrate a `requires` dependency chain:

- **`task-model.plain`** — the base module. Defines `:Task:`, `:TaskList:`, and `:User:`,
  implements the entry point, adding a task, and showing the task list. It exports its
  concepts so downstream modules can build on them.
- **`task-manager.plain`** — the top module. It `requires` `task-model`, inheriting its
  generated code and functional specs as a starting point, then adds deleting, editing,
  and completing tasks.

Rendering `task-manager.plain` builds `task-model` first, then continues on top of it.

# How to render the example

You can run the example with the `codeplain` command:

```bash
codeplain task-manager.plain
```

# How to run the generated code

After the rendering is finished, you can run the generated software code using the command:

```bash
python3 dist/taskmgr.py
```

See [top-level README](../../README.md) for additional information and help for troubleshooting the examples.
