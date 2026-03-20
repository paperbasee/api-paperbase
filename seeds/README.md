Backend seed files live in this folder.

Suggested usage:
- Keep deterministic fixture-like seed payloads here.
- Use one file per domain (e.g. `stores.seed.json`, `products.seed.json`).
- Keep sensitive data out of seed files.
- Keep Django command entrypoints in `engine/.../management/commands/`, and load data from this folder.
- Product seed data files:
  - `seeds/products/seed_products.json`
  - `seeds/products/seed_apparel_demo.json`
