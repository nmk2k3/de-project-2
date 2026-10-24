# AGENTS.md — Engineering Principles

## Core Mindset
- **Simplicity first:** Choose the simplest solution that solves the problem correctly.
- **Avoid overengineering:** Don't build abstractions or features for hypothetical future needs.
- **Think before coding:** Understand the problem, existing code, and potential impact before making changes.
- **Maintainability over cleverness:** Prefer readable, predictable code over sophisticated solutions.

## Code Structure
- `main.py` should orchestrate execution, not contain business logic.
- Separate responsibilities into clear modules when necessary.
- Keep functions focused on one responsibility.
- Avoid both giant functions and unnecessary fragmentation.
- Reuse existing code before introducing new abstractions.

## Problem Solving
- Solve the actual problem, not every possible future problem.
- Prefer incremental changes over large rewrites.
- Handle meaningful edge cases without excessive defensive programming.
- Prioritize correctness, readability, and reliability before optimization.

## Data Engineering
- Design pipelines to be idempotent whenever possible.
- Validate data at important boundaries.
- Use structured logging and meaningful error handling.
- Keep configuration separate from business logic.
- Test critical transformations and pipeline behavior.

## Testing Principles
- **Confidence over coverage:** Test meaningful behavior and risks, not arbitrary coverage targets.
- **Risk-based testing:** Prioritize critical logic, edge cases, failure paths, and regressions.
- **Test behavior, not implementation:** Focus on inputs, outputs, and observable outcomes.
- **Minimal sufficient tests:** Avoid redundant tests, excessive mocking, and unnecessary complexity.
- **Data reliability:** Verify data integrity, schema validation, and idempotency where relevant.
- **Maintainability:** Keep tests simple, readable, and resilient to refactoring.

## Development Discipline
- **Root cause first:** Fix underlying problems instead of masking symptoms.
- **Minimal changes:** Make focused changes and preserve unrelated behavior.
- **Verify your work:** Run relevant checks and never claim unverified success.
- **Explicit behavior:** Make assumptions clear; never silently ignore errors or invalid data.

## Golden Rule
**Write code that is easy to understand, easy to debug, and easy to change. Complexity must be justified, not assumed necessary.**

**Every test should protect against a meaningful bug or regression. Write only as many tests as needed to establish confidence.**