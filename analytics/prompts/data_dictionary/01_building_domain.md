# Macro Building Expenses Domain ("Gastos Comunes del Edificio")

## 1. Dual Expense Classification (`operational` vs. `fondos`)
* **Gasto Común Operacional (`operational`)**: Ordinary operating expenses of the condominium (staff payroll, utilities, maintenance, repairs, insurance, supplies, etc.) corresponding to `TOTAL GASTOS COMUNES`.
* **Fondos y Recargos (`fondos`)**: Reserve funds and special surcharges (`Fondo de Reserva 5%`, etc.) added after `TOTAL GASTOS COMUNES`.
* **Total General Cobrado (`calculated_grand_total`)**: The sum of operational expenses plus reserve funds (`operational_total + fondos_total`), corresponding to final total billed.

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

## 3. Canonical Categories
Filter case-insensitively: `LOWER(category_name) LIKE '%...%'`.
* `mantenciones y servicios` (Elevator, boiler, security cameras maintenance)
* `consumos` (Water, common gas 30%, electricity)
* `remuneraciones` (Staff salaries, social security, bonuses)
* `reparaciones y repuestos` (Emergency repairs, spare parts)
* `seguros` (Building insurance, fire protection)
* `insumos` (Cleaning supplies, pool chemicals, office stationery)
* `otros gastos` (Bank fees, miscellaneous petty cash)
* `asesorias y honorarios profesionales` (Legal, accounting, audit fees)
* `fondos` (`Fondo de Reserva 5%`, `Fondo Impermeabilizacion`, and any special funds)

## 4. Sample Queries
```sql
SELECT period_date, operational_total, fondos_total, calculated_grand_total
FROM expense_reports
WHERE period_year = 2026
ORDER BY period_date ASC;
```
