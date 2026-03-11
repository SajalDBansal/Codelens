# Checkout demo

A small repository for checking graph indexing, embedding, and cited answers.
The `checkout` function uses the helpers in pricing.py.

## Calculation order

`subtotal` adds item prices, `apply_discount` reduces that sum, and `add_tax`
applies tax to the discounted amount. `checkout` rounds the result to two
decimal places. For prices [100, 50], discount 10, and tax 20, the result is 162.
