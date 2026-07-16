# Genuine iBay Fixture Characterization

These nine browser-saved source pages were provided on 2026-07-01 and are tested from `tests/fixtures/ibay/`. They are input fixtures only; they are not dashboard data.

| Scenario | Captured evidence exercised | Expected extraction behaviour |
|---|---|---|
| IGMH 1-room apartment | MVR primary block, `12500.00` title/description, seven-digit phone | Select MVR 12,500, room 1, Male; do not choose Similar Items MVR 1,900. |
| Galolhu 1-room apartment | MVR 15,000 primary block, phone in title | Select 15,000, room 1, Male. |
| Majeedhee three-room apartment | MVR 20,000 block, monthly description, advance amount | Select 20,000; retain 15,000 as a secondary payment candidate. |
| Hulhumale three-bedroom apartment | `Square Feet: 705`, room table, MVR 26,000, advance | Read structured area and room count; classify zone HULHUMALE before MALE. |
| Orchid Magu office/apartment | Empty price block; `Rent: USD 1800/-`; `Security Deposit: USD 3600/-` | Select USD 1,800 from description; do not select the deposit. |
| Henveyru one-bedroom apartment | MVR 15,000 block; `per month` description | Select MVR 15,000 and MONTHLY frequency. |
| Machchangolhi daily rooms | MVR 500 block; daily/hourly wording | Select DAILY and do not derive a room count from unlabelled plural wording. |
| Maafannu daily rooms near shop | MVR 450; both room and shop signals | Select DAILY; use UNKNOWN listing type for conflicting residential/commercial signals. |
| Rose Hotels daily range | Title telephone numbers; MVR 300-600/day description | Reject phone numbers as price; select 300 with an ambiguous-range review reason. |

Observed selectors: `.details-page`, `.iw-details-heading h5`, `.iw-price-row .price`, `.item-info-table`, `.details-page_product-desc`, and Similar Items containers such as `#similar-items-slider-holder`.
