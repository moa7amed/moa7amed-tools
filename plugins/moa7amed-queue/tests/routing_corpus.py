"""Reference prompts for the model router, grouped by the tier they must route to
under the default (balanced) policy. Used by tests/test_router.py."""

HAIKU = [
    "Update the README to mention the new install command.",
    "Fix the typo in CONTRIBUTING.md.",
    "Add a comment explaining what the retry helper does.",
    "Fix spelling mistakes in the docs folder.",
    "Reword the error message shown when the upload is too large.",
    "Explain what each setting in appsettings.json does in a short note.",
    "Update the copyright year in the license header.",
    "Rename the files in the assets folder to lowercase.",
    "Sort the imports in utils.py.",
    "Add a CHANGELOG entry for version 2.1.",
    "Translate the README into Arabic.",
    "Remove trailing whitespace from the markdown files.",
    "Write a short summary of the project structure in docs/overview.md.",
    "Change the title text on the About page to 'About us'.",
    "Organize the files in the scripts folder into subfolders by purpose.",
]

SONNET = [
    "Add a search box with filtering to the customers table page.",
    "Build a signup form with client-side validation for email and password strength.",
    "Implement an ASP.NET MVC controller and view for editing products.",
    "Add a REST endpoint that returns the list of orders for a customer.",
    "Fix the bug in checkout.",
    "Write unit tests for the date utilities.",
    "Add pagination and sorting to the invoices grid.",
    "Add a dashboard widget showing this month's sales total.",
    "Fix the validation so the form rejects an empty email address.",
    "Refactor the report generator to extract a helper for date formatting.",
    "Add integration tests for the products API.",
    "Add dark mode support to the settings page.",
    "Implement CSV export for the master data search results.",
    "The save button does nothing when the name contains a quote. Fix it.",
    "Add a dropdown to filter the task list by status.",
    "Create a new endpoint POST /api/notes with validation and a unit test.",
    "Make the sidebar collapsible and remember the state in local storage.",
    "Handle the case where the uploaded file has no extension.",
]

OPUS = [
    "Design the database schema and write migrations to split the orders table into orders and "
    "order_items with zero downtime; update all repositories and transactions.",
    "Fix the deadlock in the payment worker.",
    "Why does login randomly fail in production?",
    "Migrate auth to OAuth2.",
    "Fix the memory leak in the image cache.",
    "Refactor the entire data access layer to use the repository pattern across all modules.",
    "Investigate the root cause of the intermittent 500 errors on the checkout API.",
    "Redesign the permission system to support role-based access control per tenant.",
    "The nightly sync between the ERP and the warehouse service sometimes loses records. "
    "Find out why and fix it.",
    "Find and fix the performance bottleneck that makes the reports page take 40 seconds.",
    "Re-architect the notification module into an event-driven design.",
    "There is a race condition in the session cache.",
    "Write the data migration that backfills the new tenant_id column on the production database "
    "without locking the tables.",
    "Fix the SQL injection vulnerability in the search endpoint and audit the other queries.",
    "Split the monolith's billing code into its own service.",
    "Users are intermittently logged out; it only happens in production under load.",
    "Introduce multi-tenant data isolation across all services.",
]

# Short prompts that are hard: none of them may go to the smallest model.
SHORT_BUT_HARD = [
    ("Fix the deadlock in the payment worker.", "opus"),
    ("Race condition in cache.", "opus"),
    ("Fix memory leak.", "opus"),
    ("Migrate auth to OAuth2.", "opus"),
    ("Fix checkout bug.", "sonnet"),
    ("Make search faster.", "sonnet"),
    ("Add login.", "sonnet"),
    ("Fix the crash.", "sonnet"),
    ("Refactor billing.", "sonnet"),
]

# Technical words inside a documentation request are the subject, not the work.
DOCS_ABOUT_TECHNICAL_TOPICS = [
    "Update the docs for the new database migration command.",
    "Document how to run the unit tests in the README.",
    "Explain what the config file does.",
]

# A large documentation job is not "simple documentation", but it is not opus work either.
LARGE_DOCS = [
    "Document the public API of the billing module in docs/billing.md with examples for each "
    "endpoint, error codes, authentication notes and a changelog; also add docstrings to each "
    "public function.",
]

# Prose output that still needs the strongest reasoning.
REASONING_HEAVY_WRITING = [
    "Explain why login randomly fails in production.",
    "Write a design document for migrating authentication to OAuth2, comparing the options.",
    "Write a migration plan for moving the orders database to PostgreSQL with zero downtime.",
]

# Hard work mentioned next to a documentation chore is still hard work.
MIXED_WITH_DOCS = [
    ("Redesign the permission system to support role-based access control per tenant. "
     "Update the README.", "opus"),
    ("Update the README.\nThen fix the deadlock in the payment worker.", "opus"),
    ("Add a search box to the customers page and document it in the README.", "sonnet"),
    ("Tune the connection pool; requests hang under load. Also fix the typo in the docs.", "opus"),
]

# Requests too vague to classify with confidence: sonnet, flagged for review.
VAGUE = ["Add login.", "Handle the thing we discussed.", "Fix it."]

# Where the three policies differ: (prompt, balanced, quality_first, cost_optimized).
POLICY_DIFFERENCES = [
    ("Refactor the order service and its repository to remove the duplicated validation logic "
     "while keeping backward compatibility for existing clients.", "sonnet", "opus", "sonnet"),
    ("Fix the SQL injection vulnerability in the search endpoint and audit the other queries.",
     "opus", "opus", "sonnet"),
    ("Update the README, the CONTRIBUTING guide and the docs folder: fix the headings, add "
     "install steps for Windows, macOS and Linux, document every configuration option, and add "
     "a troubleshooting section.", "haiku", "sonnet", "haiku"),
    # Unmistakably hard or unmistakably trivial tasks route the same way under every policy.
    ("Fix the deadlock in the payment worker.", "opus", "opus", "opus"),
    ("Fix the typo in CONTRIBUTING.md.", "haiku", "haiku", "haiku"),
    ("Add a search box with filtering to the customers table page.", "sonnet", "sonnet", "sonnet"),
]

# A negated mention must not count as a signal.
NEGATED = [
    ("Update the README. No database changes and do not touch the authentication code.", "haiku"),
    ("Add a tooltip to the save button without changing the schema or any migrations.", "sonnet"),
]
