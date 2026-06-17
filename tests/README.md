# Tests

This folder contains lightweight smoke tests for the food-order-pipeline project.

## Smoke test

The smoke test verifies:

- API health
- restaurant/courier simulator health
- dashboard summary response shape
- order creation
- idempotent duplicate order creation
- automatic lifecycle progression to DELIVERED
- expected lifecycle events
- cancellation before worker processing
- cancelled order remains CANCELLED after worker restart

Run after the system is started:

```bash
./tests/smoke-test.sh
