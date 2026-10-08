## Python

1. Functions, classes, methods, global variables, etc. should be placed alphabetically within logical groups in each module. Exceptions are allowed when ordering would break execution or make the code difficult to understand, including dependencies, Pydantic validators, and type definitions.
2. When calling functions or methods with multiple arguments, use keyword arguments in alphabetical order, except where the API requires positional arguments. Single-argument calls may use positional arguments.
3. Docstrings are always in NumPy format.
4. Use blank lines to separate logical sections within function and control-block bodies where they improve readability.
5. Always use type hints in function and method signatures. Elsewhere, use them when helpful. The repo's `mypy` tool checks type hints.
6. Do not call external APIs or paid services in tests. Mock or stub those boundaries. Local MCP client/server integration checks are allowed. If external mocking would leave important behavior unverified, explain the gap and await my decision.
7. Always reuse existing pytest fixtures when possible. If a new fixture is needed, it should be added to the `conftest.py` file in the appropriate directory.
8. Private function/method/class/variable names should begin with an underscore to distinguish against their public counterparts. Preserve framework-required names and established public interfaces.
