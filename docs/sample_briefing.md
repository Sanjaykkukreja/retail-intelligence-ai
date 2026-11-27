# GlowBasket Daily Briefing - Mon 23 Nov 2026

## Numbers for 2026-11-23

- **Net sales:** Rs 65,363  (+3.3% vs 7-day average, -5.4% vs same weekday, -15.7% vs previous day)
- **Orders:** 90   **Average order value:** Rs 726
- **Gross margin:** 56.6%  (target 30%)   **Discount given:** 4.2% of list price
- **Cancelled orders:** 1   **Returned-to-origin:** 0

Top sellers: Vitamin C Serum 30ml (Rs 7,750); Honey Sunscreen Spf50 50g (Rs 4,602); Peptide Body Lotion 200ml (Rs 3,581); Centella Body Lotion 200ml (Rs 2,347); Onion Primer 30ml (Rs 2,307)

## What needs your attention (5)

1. **[HIGH] 73 paid orders have not been dispatched after 4+ days (oldest 2025-11-29)** - about Rs 57,451 at stake (estimate)
   - Do: Dispatch or refund each order this week and add an alert for any paid order older than 48 hours without dispatch.
   - How we estimated it: Value = taxable value (ex-GST) of the stuck orders.
2. **[HIGH] Peptide Eye Cream 15g: 114 of 130 units will not sell before 2027-01-07 (45 days)** - about Rs 33,437 at stake (estimate)
   - Do: Best of the discounts tested is 20% off: about 21 units sell and 12,451 INR is recovered (ex-GST), leaving 109 units to write off. Markdown alone will not clear this batch, so push the supplier-return, bundle or liquidation route first. Assumes demand rises 1.5% per 1% discount, which is not measured from your data.
   - How we estimated it: Units at risk = batch quantity minus (28-day run-rate x days to expiry), after older batches of the same SKU sell first. Value at landed cost.
3. **[HIGH] 12 SKUs sold below the 30% margin target at 25%+ discount (2026-11-01 to 2026-11-10)** - about Rs 24,808 at stake (estimate)
   - Do: Set a discount floor per SKU = the largest discount that still earns the target margin (column max_discount_for_target_pct); exclude thin-margin SKUs from sitewide sales.
   - How we estimated it: Shortfall = target margin x revenue minus actual gross margin on lines discounted 25%+ (before refunds).
4. **[MEDIUM] 48 orders shipped from the warehouse have no invoice (about 88 units)** - about Rs 36,822 at stake (estimate)
   - Do: Invoice them. Shipments without invoices can also be a GST compliance problem.
   - How we estimated it: Value = units x average net price per unit (ex-GST), an approximation.
5. **[MEDIUM] Niacinamide Face Wash 100ml out of stock 3 days (2026-11-21 to 2026-11-23) - still out** - about Rs 9,292 at stake (estimate)
   - Do: Expedite the open purchase order, switch the product page to 'notify me' and pause ads on this SKU until stock lands.
   - How we estimated it: Estimated lost units = in-stock average of 12.18/day in the 28 days before x 3 days; revenue = units x average net price (ex-GST).

## Questions for you

- Why are these orders stuck: out of stock, pick-and-pack, address problem, or a billing-to-warehouse sync failure?  _(ref paid_not_dispatched:all:2026-11-23)_
- Can batch B25111901 be returned to the supplier, bundled into a gift set or sold to a liquidator? Is a markdown acceptable?  _(ref expiry_risk:8901019898510:B25111901:2026-11-23)_
- Were these deep discounts intentional (clearance) or a promotion set-up error? Who approved a 35% discount on thin-margin SKUs?  _(ref discount_leakage:all:2026-11-01)_
- Are these phone or WhatsApp orders? Who is responsible for invoicing them?  _(ref dispatched_not_billed:all:2026-11-23)_
- Why was Niacinamide Face Wash 100ml out of stock from 2026-11-21? A delayed purchase order, a quality hold, or planned? If a PO is delayed, what is the new arrival date?  _(ref stockout:8901000104729:2026-11-21)_

## Decisions waiting for you (6)

- #2 Peptide Eye Cream 15g: 114 of 130 units will not sell before 2027-01-07 (45 days)
- #3 12 SKUs sold below the 30% margin target at 25%+ discount (2026-11-01 to 2026-11-10)
- #4 48 orders shipped from the warehouse have no invoice (about 88 units)
- #5 Niacinamide Face Wash 100ml out of stock 3 days (2026-11-21 to 2026-11-23) - still out
- #6 Centella Moisturizer 100g: 77 of 123 units will not sell before 2027-01-08 (46 days)
- #7 9 selling SKUs have less than 7 days of cover

Approve with `python -m retail_intel approve <#> "note"` or on the Recommendations screen.

## Data health

- billing duplicate rows: 211 (identical invoice lines repeated; extra copies are dropped)
- wms negative stock rows: 3 (negative stock rows are ignored, not netted)
- orders paid not dispatched: 73 (paid orders with no dispatch after 4 days)
- orders dispatched not billed: 48 (shipped from the warehouse but no invoice)

_Data through 2026-11-23. Revenue is net of discounts, refunds and GST; cancelled and returned-to-origin orders excluded. Estimates are marked; recommendations need your approval._
