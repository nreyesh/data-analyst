# Cross-Domain Mathematical & Schema Bridge (Unit vs. Building)

## 1. Relational Keys
Unit bills and building expense reports are aligned chronologically by `period_date`:
```sql
SELECT u.unit_code, u.period_date, u.calculated_total_month, r.operational_total, r.calculated_grand_total
FROM unit_bills u
JOIN expense_reports r ON u.period_date = r.period_date
WHERE u.unit_code = 'B-504';
```

## 2. Prorating Relationships (Ley de Copropiedad 21.442)

1. **Base Gasto Común Prorating:**
   The `Gasto Común` line item in `unit_bill_concepts` is calculated by applying the unit's `alicuota_total` percentage to the building's `operational_total`:
   $$\text{Base Gasto Común} = \text{ROUND}\left( \text{expense\_reports.operational\_total} \times \frac{\text{unit\_bills.alicuota\_total}}{100} \right)$$
   *Example:* $\$55.709.656 \times (0.16736 / 100) = \$93.236 \text{ CLP}$ (exact match).

2. **Central Boiler Metered Tariff (`unit_rate`):**
   The hot water rate in `unit_bill_consumptions.unit_rate` (e.g. $\$8.340,24 / m^3$) is determined by dividing total central boiler fuel/gas expenses (in `expense_items` where `LOWER(normalized_name) LIKE '%gas%'` or `'caldera'`) by total community $m^3$ consumed. When boiler gas costs surge in winter, `unit_rate` increases even if the resident's personal consumption ($m^3$) remains constant.

3. **Special Funds Bridge:**
   Fund quotas in `unit_bill_concepts` (such as `Fondo Impermeabilizacion` or `Fondo de Reserva 5%`) correlate directly with sections in `expense_sections` where `expense_type = 'fondos'`.

## 3. Two-Part Causal Queries
When investigating why a unit bill increased:
- **Query 1 (Unit Delta):** Compares month-over-month differences in `calculated_dept_subtotal`, `calculated_consumo_total`, and `calculated_unit_subtotal` for `WHERE unit_code = :target_unit`.
- **Query 2 (Building Drivers):** Compares building operational categories and fuel items between baseline and peak periods using `expense_sections` and `expense_reports`.
