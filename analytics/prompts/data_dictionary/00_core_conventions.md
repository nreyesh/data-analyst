# Domain Conventions & Financial Rules

* **Currency (CLP):** All amounts are signed integers representing Chilean Pesos (CLP). Decimals or cents are strictly prohibited.
* **Negative Values (Credit Notes):** Negative numbers in `expense_items.amount` represent credit notes, discounts, refunds, or reversals.
* **Billing Periods:** Normalized strictly to the first day of each month: `'YYYY-MM-01'` (e.g. `'2025-07-01'`).
* **Chronological Ordering:** Order queries by `ORDER BY period_date ASC` or `ORDER BY period_year ASC, period_month ASC`.
