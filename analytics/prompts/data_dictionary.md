# Knowledge Base: Chilean Building Expenses Data Dictionary ("Gastos Comunes")

## 1. Domain Conventions & Financial Rules

* **Currency (CLP):** All amounts are signed integers representing Chilean Pesos (CLP). Decimals or cents are strictly prohibited.
* **Dual Expense Classification (`operational` vs. `fondos`):**
  * **Gasto Común Operacional (`operational`)**: Ordinary operating expenses of the condominium (staff payroll, utilities, maintenance, repairs, insurance, supplies, etc.) corresponding to `TOTAL GASTOS COMUNES` on the monthly statement.
  * **Fondos y Recargos (`fondos`)**: Reserve funds and special surcharges (`Fondo de Reserva 5%`, `Fondo Impermeabilizacion`, etc.) added after `TOTAL GASTOS COMUNES`.
  * **Total General Cobrado (`calculated_grand_total`)**: The sum of operational expenses plus reserve funds (`operational_total + fondos_total`), corresponding to the final `TOTAL` billed to residents.
* **Negative Values (Credit Notes):** Negative numbers in `expense_items.amount` represent credit notes, discounts, refunds, or reversals.
* **Billing Periods:** Normalized strictly to the first day of each month: `'YYYY-MM-01'` (e.g. `'2025-07-01'`).
* **Chronological Ordering:** Order queries by `ORDER BY r.period_date ASC` or `ORDER BY r.period_year ASC, r.period_month ASC`.

---

## 2. Table Specifications

### Table: `expense_reports`
Header table containing monthly building expense summaries.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | VARCHAR(36) | PRIMARY KEY | Time-ordered UUIDv7 unique identifier. |
| `period_date` | VARCHAR(10) | UNIQUE, INDEXED | Period date formatted strictly as `'YYYY-MM-01'`. |
| `period_year` | INTEGER | INDEXED | Four-digit calendar year (e.g. `2025`, `2026`). |
| `period_month` | INTEGER | INDEXED | Calendar month number (1 to 12). |
| `operational_total` | INTEGER | NOT NULL | Sum of operational expense sections (`TOTAL GASTOS COMUNES`) in CLP. |
| `fondos_total` | INTEGER | NOT NULL | Sum of reserve funds and special surcharges (`Fondos`) in CLP. |
| `calculated_grand_total` | INTEGER | NOT NULL | Final monthly total (`operational_total + fondos_total`) in CLP. |
| `created_at` | VARCHAR(32) | NOT NULL | ISO 8601 creation timestamp. |

### Table: `expense_sections`
Categorical sections grouping related expense line items for a report.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | VARCHAR(36) | PRIMARY KEY | Time-ordered UUIDv7 unique identifier. |
| `report_id` | VARCHAR(36) | FOREIGN KEY | References `expense_reports.id` (ON DELETE CASCADE). |
| `category_name` | VARCHAR | NOT NULL | Normalized lowercase category name. |
| `expense_type` | VARCHAR(32) | NOT NULL | `'operational'` for operating sections or `'fondos'` for reserve/special funds. |
| `calculated_subtotal` | INTEGER | NOT NULL | Subtotal in Chilean Pesos (CLP) for this category. |
| `display_order` | INTEGER | DEFAULT 0 | Sequential presentation order in original statement. |

### Table: `expense_items`
Individual vendor invoices, line-item charges, and fund contributions within a section.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | VARCHAR(36) | PRIMARY KEY | Time-ordered UUIDv7 unique identifier. |
| `section_id` | VARCHAR(36) | FOREIGN KEY | References `expense_sections.id` (ON DELETE CASCADE). |
| `name` | VARCHAR | NOT NULL | Original line item description / invoice vendor text / fund name. |
| `normalized_name` | VARCHAR | INDEXED | Normalized lowercase string without accents (preserving `'ñ'`). |
| `amount` | INTEGER | NOT NULL | Item cost in CLP. Negative for credit notes/discounts. |
| `display_order` | INTEGER | DEFAULT 0 | Sequential display order within the section. |

---

## 3. Canonical Categories

When filtering by category, always match case-insensitively: `LOWER(category_name) LIKE '%...%'`.

### Operational Categories (`expense_type = 'operational'`)
* `mantenciones y servicios` (Elevator, boiler, security cameras maintenance)
* `consumos` (Water, common gas 30%, electricity)
* `remuneraciones` (Staff salaries, social security, bonuses)
* `reparaciones y repuestos` (Emergency repairs, spare parts)
* `seguros` (Building insurance, fire protection)
* `insumos` (Cleaning supplies, pool chemicals, office stationery)
* `otros gastos` (Bank fees, miscellaneous petty cash)
* `asesorias y honorarios profesionales` (Legal, accounting, audit fees)

### Reserve & Special Funds (`expense_type = 'fondos'`)
* `fondos` (`Fondo de Reserva 5%`, `Fondo Impermeabilizacion`, and any special building funds)

---

## 4. Canonical Sample Queries

### Monthly Total Evolution (Operational, Fondos, and Final Total)
```sql
SELECT 
    period_date,
    period_month,
    operational_total AS operational_clp,
    fondos_total AS fondos_clp,
    calculated_grand_total AS total_clp
FROM expense_reports
WHERE period_year = 2026
ORDER BY period_month ASC;
```

### Category Trend Over Time
```sql
SELECT 
    r.period_date,
    s.expense_type,
    s.calculated_subtotal AS subtotal_clp
FROM expense_sections s
JOIN expense_reports r ON s.report_id = r.id
WHERE LOWER(s.category_name) LIKE '%mantencion%'
  AND r.period_date BETWEEN '2025-07-01' AND '2025-12-01'
ORDER BY r.period_date ASC;
```

### Year-over-Year Category Comparison
```sql
SELECT 
    s.category_name,
    s.expense_type,
    r.period_year,
    SUM(s.calculated_subtotal) AS annual_subtotal_clp
FROM expense_sections s
JOIN expense_reports r ON s.report_id = r.id
WHERE r.period_year IN (2025, 2026)
GROUP BY s.category_name, s.expense_type, r.period_year
ORDER BY s.expense_type, s.category_name, r.period_year;
```
