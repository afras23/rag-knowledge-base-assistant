## Known Issues

### Database migrations (Alembic)
- `alembic downgrade base` fails
- Causes all integration tests to fail
- Likely causes:
  - incorrect migration state
  - missing downgrade logic
  - DB not in sync with migrations

### Impact
- Integration tests unreliable
- Does NOT block current development phase (ingestion pipeline)

### Plan
- Fix after ingestion pipeline is complete
- Validate full migration lifecycle:
  - upgrade → downgrade → upgrade