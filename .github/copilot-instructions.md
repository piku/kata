# Codebase Context for Kata

This file provides context for agentic coding agents working on the Kata project.

## Build/Lint/Test Commands
*   **Build:** Not explicitly defined, Python is interpreted.
*   **Lint:** No standard lint command found.
*   **Typecheck:** No standard typecheck command found.
*   **Test:** `make test` or `python kata.py`. To run a single test, refer to the kata.py script structure. There is no dedicated test runner.

## Code Style Guidelines
*   **Language:** Python 3.12+
*   **Imports:** Group standard library, third-party, and local imports. Explicitly import symbols, not entire modules.
*   **Formatting:** Follow PEP 8. Use `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for constants.
*   **Types:** Use type hints.
*   **Naming Conventions:** `snake_case` for functions/variables, `UPPER_SNAKE_CASE` for constants.
*   **Error Handling:** Use `try...except`. Print errors using `echo` with `fg='red'`.
*   **Docstrings:** Include docstrings for modules and functions.
*   **CLI:** Uses the `click` library for command-line interfaces.

## Purpose and Functionality
*   Parse a `kata-compose.yaml` file to generate a `docker-compose.yaml` file and a Caddy configuration file.
*   **Web Server:** Uses Caddy, configured via its admin API.
