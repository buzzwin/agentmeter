# Contributing

Start with the README development commands and run the tests before submitting a
pull request. For a bug, include a minimal reproducible example, Python version,
expected result, and actual result. Do not include customer data or secrets.

Keep runtime dependencies out of the core. Put provider and framework behavior
behind adapters. Changes to accounting rules need numerical examples and tests;
changes to exported fields need a schema compatibility plan. Document denominator,
currency, precision, and double-counting implications. Favor small pull requests
with a clear problem statement and relevant verification.

The event schema is version 1; this alpha API may evolve before a stable release.
Do not silently change existing field meanings. Release checks should include the
full test suite, both examples, wheel/source builds, and a clean wheel installation.

Be respectful and constructive in issues and reviews. Report suspected security
issues privately to the repository maintainers through the host's private
vulnerability-reporting facility if enabled; do not publish working exploits or
sensitive data in public issues.
