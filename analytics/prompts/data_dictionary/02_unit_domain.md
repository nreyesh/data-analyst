# Unit-Level Apartment Expenses Domain ("Boleta de Cobro por Unidad")

## 1. Domain Structure
Each unit billing statement (*"Nota de Cobro"*, e.g. Depto `B-504`) contains three distinct components:
1. **Conceptos Departamento (`unit_bill_concepts`)**: Base prorated gasto común plus special funds (e.g. `Gasto Común`, `Fondo de Reserva 5%`, `Fondo Impermeabilizacion`). Subtotal stored in `unit_bills.calculated_dept_subtotal`.
2. **Consumos Generales (`unit_bill_consumptions`)**: Metered individual utility consumption, primarily central hot water (Torre B/A) with previous reading, current reading, consumption volume ($m^3$), unit rate ($\$/m^3$), and total. Subtotal stored in `unit_bills.calculated_consumo_total`.
3. **Gastos por Unidad (`unit_bill_charges`)**: Apartment-specific surcharges, fines, fixed fees (e.g. `Fondo de Indemnizacion`, parking fees). Subtotal stored in `unit_bills.calculated_unit_subtotal`.
4. **Total a Pagar del Mes (`calculated_total_month`)**: `calculated_dept_subtotal + calculated_consumo_total + calculated_unit_subtotal`.

## 2. Table Specifications

### Table: `unit_bills`
Header table containing monthly unit statement summaries.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | VARCHAR(36) | PRIMARY KEY | Time-ordered UUIDv7 unique identifier. |
| `unit_code` | VARCHAR(32) | INDEXED, NOT NULL | Canonical composite code e.g. `'B-504'`, `'B-506'`. |
| `tower` | VARCHAR(16) | INDEXED, NOT NULL | Building tower identifier e.g. `'B'`, `'A'`. |
| `unit_number` | INTEGER | INDEXED, NOT NULL | Apartment number integer e.g. `504`, `506`. |
| `period_date` | VARCHAR(10) | INDEXED, NOT NULL | Period date formatted strictly as `'YYYY-MM-01'`. |
| `period_year` | INTEGER | NOT NULL | Four-digit calendar year (e.g. `2026`). |
| `period_month` | INTEGER | NOT NULL | Calendar month number (1 to 12). |
| `alicuota_total` | FLOAT | NOT NULL | Prorate percentage e.g. `0.16736` (meaning `0.16736%`). |
| `last_payment_date` | VARCHAR(10) | NULLABLE | ISO date `'YYYY-MM-DD'` of last payment. |
| `last_payment_amount` | INTEGER | NULLABLE | Last payment amount in CLP. |
| `calculated_dept_subtotal` | INTEGER | NOT NULL | **Subtotal Depto (Cuota Base + Fondos)** in CLP. |
| `calculated_consumo_total` | INTEGER | NOT NULL | **Agua Caliente** in CLP. |
| `calculated_unit_subtotal` | INTEGER | NOT NULL | **Cargos Particulares (Multas y otros)** in CLP. |
| `calculated_total_month` | INTEGER | NOT NULL | **Total Cobrado en el Mes** in CLP. |

### Table: `unit_bill_concepts`
Breakdown rows in "Tabla Conceptos" (Base prorating and individual fund quotas).

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | VARCHAR(36) | PRIMARY KEY | Time-ordered UUIDv7 unique identifier. |
| `unit_bill_id` | VARCHAR(36) | FOREIGN KEY | References `unit_bills.id` (ON DELETE CASCADE). |
| `concept_name` | VARCHAR | NOT NULL | Name e.g. `'Gasto Común'`, `'Fondo de Reserva 5%'`. |
| `normalized_name` | VARCHAR | INDEXED, NOT NULL | Lowercase accent-stripped name. |
| `detail` | VARCHAR | NULLABLE | Detail string e.g. `'0,16736 %'`, `'5,00 %'`. |
| `amount` | INTEGER | NOT NULL | Payable amount in CLP. |
| `display_order` | INTEGER | DEFAULT 0 | Sequential display order. |

### Table: `unit_bill_consumptions`
Breakdown rows in "Tabla Consumos Generales" (Metered hot water and utilities).

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | VARCHAR(36) | PRIMARY KEY | Time-ordered UUIDv7 unique identifier. |
| `unit_bill_id` | VARCHAR(36) | FOREIGN KEY | References `unit_bills.id` (ON DELETE CASCADE). |
| `item_name` | VARCHAR | NOT NULL | Item name e.g. `'Torre B'`, `'Torre A'`. |
| `normalized_name` | VARCHAR | INDEXED, NOT NULL | Lowercase accent-stripped name. |
| `previous_reading` | FLOAT | NOT NULL | Previous meter reading in $m^3$. |
| `current_reading` | FLOAT | NOT NULL | Current meter reading in $m^3$. |
| `consumption` | FLOAT | NOT NULL | Volume consumed in $m^3$ (`current - previous`). |
| `unit_rate` | FLOAT | NOT NULL | Tariff per $m^3$ in CLP (e.g. `8340.24`). |
| `total_amount` | INTEGER | NOT NULL | Total metered cost in CLP (`consumption * unit_rate`). |
| `display_order` | INTEGER | DEFAULT 0 | Sequential display order. |

### Table: `unit_bill_charges`
Breakdown rows in "Tabla Gastos por Unidad" (Fines, fixed charges, recargos).

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | VARCHAR(36) | PRIMARY KEY | Time-ordered UUIDv7 unique identifier. |
| `unit_bill_id` | VARCHAR(36) | FOREIGN KEY | References `unit_bills.id` (ON DELETE CASCADE). |
| `charge_name` | VARCHAR | NOT NULL | Charge description e.g. `'Fondo de Indemnizacion'`. |
| `normalized_name` | VARCHAR | INDEXED, NOT NULL | Lowercase accent-stripped name. |
| `charge_type` | VARCHAR(32) | NOT NULL | Category e.g. `'fixed_charge'`, `'fine'`. |
| `amount` | INTEGER | NOT NULL | Surcharge amount in CLP. |
| `display_order` | INTEGER | DEFAULT 0 | Sequential display order. |

## 3. Sample Queries

### Unit Monthly Evolution (Mandatory 4 Reconciled Components)
```sql
SELECT 
    period_date, 
    calculated_dept_subtotal, 
    calculated_consumo_total, 
    calculated_unit_subtotal, 
    calculated_total_month
FROM unit_bills
WHERE tower = 'B' AND unit_number = 504
ORDER BY period_date ASC;
```

### Hot Water Consumption and Tariff Evolution
```sql
SELECT u.period_date, c.consumption AS consumption_m3, c.unit_rate AS unit_rate_clp, c.total_amount AS total_clp
FROM unit_bill_consumptions c
JOIN unit_bills u ON c.unit_bill_id = u.id
WHERE u.tower = 'B' AND u.unit_number = 504
ORDER BY u.period_date ASC;
```

### Multi-Unit Comparison (Admin)
```sql
SELECT unit_code, tower, unit_number, period_date, calculated_total_month
FROM unit_bills
WHERE (tower = 'B' AND unit_number IN (504, 506))
ORDER BY period_date ASC, unit_code ASC;
```
