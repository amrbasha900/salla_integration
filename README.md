### Salla Integration

Professional ERPNext integration with Salla e-commerce platform

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app salla_integration
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/salla_integration
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit

## Add a new Salla endpoint (monthly additions)

Use this checklist to add new APIs consistently:

1) Client method
- Add a method in `salla_integration/utils/salla_client.py` mirroring the REST path.
- Accept `page` and `per_page` where applicable; cap with `MAX_PER_PAGE` (60) per docs.

2) API module function
- Create a function under `salla_integration/api/` (e.g., `shipments.py`).
- Keep mapping logic isolated and idempotent; prefer `get_or_create_*` helpers.

3) Sync orchestration
- Add a `sync_store_<entity>` function in `salla_integration/api/sync.py`.
- Fetch data using `client.get_all_pages(...)` and wrap with a `Salla Sync Log` via `create_sync_log`.

4) Custom fields
- If the ERPNext DocType needs new link/ID/flag fields, add them in `setup/install.py` using `create_custom_fields`.

5) Hooks and flags
- Set `salla_is_from_salla = 1` and store Salla IDs on created docs.
- If needed, add doc events to push updates back to Salla.

References: Pagination and per_page=60, and rate limits are described in Salla docs: [Pagination](https://docs.salla.dev/421124m0) • [Rate Limiting](https://docs.salla.dev/421125m0)
