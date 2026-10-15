# Development Rules

- Python 3.11+, src layout, package name `stock_repost`.
- Use `Decimal` for all money and quantities. Never use floats for stock or monetary calculations.
- Use dataclasses, frozen where sensible, full type hints. `mypy --strict` and `ruff check` must pass with zero errors.
- Allowed dependencies: pytest, hypothesis, ruff, mypy only. No ERPNext or Frappe dependency.
- Never modify or delete an existing test to make it pass. If a test appears incorrect, stop and explain.
- No placeholders, TODOs, or `pass` stubs in finished code.
- Work in phases. After each phase, always run `pytest -q`, `ruff check .`, and `mypy src` and show the real output. If configuration is missing, create it in the same phase.
- Do not begin the next phase until I approve.
- Keep functions small and pure where possible. The FIFO queue must not perform I/O.
- Every public function and class must have a docstring explaining its purpose and rationale.
- Do not create files outside the agreed project structure without asking.
- Never edit AGENTS.md without my approval.
- Write tests before implementation in each phase.
- Run `git init` now. Make one commit at the end of each approved phase. Never commit failing code.
