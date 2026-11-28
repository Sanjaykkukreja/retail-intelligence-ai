# 08 Question library

Hundreds of questions collapse into a few dozen **question families**. A family is one tested tool (or a few) that answers many questions by changing its settings: "orders delayed 2+ days", "cancelled orders last week" and "COD orders not shipped" are all `order_list` with different settings. Nobody writes new code per question.

Today: **24 families**, 17 certified, 6 waiting for data, 1 to build next.

The library lives in `retail_intel/catalog/question_families.toml`. Each family has:
- **status**: certified (tested tools today), build next (data exists, tool not built), or needs data (tells you what to ask the client for)
- **match**: words that identify it in a question. Routing runs the family's standing check for any agent allowed to use it
- **examples**: real questions. A test asks every certified example and fails if routing does not reach the family (on day one it found 3 routing bugs)

## How a question is answered
1. **Certified family**: the question's words or type name a family; its tested tool runs; same question, same answer.
2. **Exploratory**: nothing certified fits; the agent writes a one-off read-only query. The answer is labelled *Exploratory: not a certified metric*.
3. **Promotion**: the Questions asked page shows which questions keep landing on the exploratory path; the owner asks for one to be certified; we build the family with a test. Across clients, the library compounds.

## The families

### All roles

| Family | Status | Example questions | Needs |
|---|---|---|---|
| Orders by status, age, stock and payment | certified | Which orders are delayed by more than 2 days?; How many orders were cancelled last week?; Which orders did we take when the product was out of stock? | - |
| Owner tells the system something (answers, rules, decisions) | certified | The Niacinamide stockout was a supplier delay; Our margin target is 35% | - |

### Chief of staff

| Family | Status | Example questions | Needs |
|---|---|---|---|
| How is the business doing | certified | How is the business doing?; How was yesterday versus a normal day?; Net sales this month | - |
| What changed and why | certified | Why did sales drop in mid-October?; What happened to sales on 14 September? | - |
| Dispatch speed, cancellations and RTO rates | certified | How fast are we dispatching orders this month?; Is the warehouse keeping the 48-hour promise? | - |
| Website traffic, conversion and checkout | certified | How is website conversion trending?; Is the checkout working properly? | - |
| Decisions waiting and what came of past ones | certified | What is waiting for my approval?; Did the reorder we approved fix the stockout? | - |
| Contribution margin per order (CM1, CM2) | needs data | Are we making money on each order before marketing?; Is CM2 positive this month? | shipping and RTO cost per order, payment gateway and marketplace fees, ad spend by channel |

### Replenishment planner

| Family | Status | Example questions | Needs |
|---|---|---|---|
| Stock on hand and days of cover | certified | How many days of stock do we have of the vitamin C serum?; Which SKUs have under a week of cover? | - |
| Stockouts and lost sales | certified | What is out of stock right now?; How much did the serum stockout cost us? | - |
| Sell-through, stock turn and cash in stock | certified | What is our sell-through by category this quarter?; How fast is stock turning?; How much cash is tied up in stock, by category? | - |
| Slow movers, expiry and markdown options | certified | What should I do with stock about to expire?; Which products are not moving? | - |
| What to reorder, how much, by when | needs data | What should I reorder this week and how much?; Will we stock out before the next PO lands? | supplier lead time per SKU, minimum order quantity, open purchase orders |
| Goods received versus ordered | needs data | Did the supplier deliver the full PO?; Which POs are late? | purchase orders |

### Margin controller

| Family | Status | Example questions | Needs |
|---|---|---|---|
| Margin by product, category and period | certified | Which categories are below our margin target?; Worst 10 SKUs by margin last month | - |
| Discounts that lose margin | certified | Did the Diwali sale lose us money?; Which discounts are below target margin? | - |
| Did the markdown work (lift, sell-through, margin given up) | build next | Did the 30% markdown on the eye cream clear the stock?; Are customers waiting for discounts? | markdown_effect: sales and margin before vs during a discount window, per SKU |
| Returns by reason and category | certified | Why are returns high in makeup?; How many returns are shade mismatch? | - |

### Revenue assurance (finance)

| Family | Status | Example questions | Needs |
|---|---|---|---|
| Orders falling between website, billing and warehouse | certified | Are any paid orders stuck?; Which shipments have no invoice? | - |
| Refunds, cancellations and RTO | certified | How much did we refund last month?; What is our RTO rate on COD? | - |
| COD cash collected versus remitted by courier | needs data | Has the courier paid us for all delivered COD orders?; How much COD cash is pending with couriers? | courier COD remittance reports |
| Marketplace payouts versus orders, fees and return credits | needs data | Were we overcharged on commission or weight?; Which return credits are missing? | marketplace settlement reports |
| TCS and TDS deducted versus claimed | needs data | Have we claimed all the TCS the marketplaces deducted? | marketplace TCS statements, GST returns |
| Can we trust the numbers | certified | Are there duplicate invoices?; Is the data complete? | - |

## What the research added

- **Finance (India D2C):** the biggest silent leaks are COD remittance (couriers hold cash 7-10 days and net off fees and TDS), marketplace commission and weight overcharges, missing return credits (claim windows: Amazon 90 days, Flipkart 14 days) and unclaimed TCS. One source puts hidden reconciliation losses at roughly 2-5% of revenue. All need data streams we do not have yet: these are the `needs_data` families and the strongest case for the Zoho and marketplace connectors.
- **Replenishment:** planners decide what to reorder first by profit at risk, when to order given lead time, whether MOQ forces overstock, and how fast stock turns. Lead time and MOQ are not in our data, so *what and how much to reorder* is `needs_data`.
- **Merchandising:** markdown questions are depth, sell-through during the markdown, sales lift, margin given up, and whether customers wait for discounts. `markdown_effectiveness` is the next family to build.
- **Founders:** weekly CM1 (after product cost, fees, shipping and RTO) and CM2 (after ad spend). Needs fees, shipping and ad-spend streams.

## Sources

- Inventory Planner, 7 key metrics to figure out when to order inventory: https://www.inventory-planner.com/7-key-metrics-to-figure-out-when-to-order-inventory/
- Base, Payment reconciliation for D2C brands in India: https://base.com/en-IN/blog/payment-reconciliation-for-d2c-brands-in-india/
- Umbrex, Markdown strategy and effectiveness: https://umbrex.com/resources/industry-analyses/how-to-analyze-a-retail-company/markdown-strategy-and-effectiveness/
- eChai Ventures, D2C unit economics to track weekly: https://echai.ventures/d2c/d2c-accounting-finance/unit-economics-numbers-to-track-weekly
