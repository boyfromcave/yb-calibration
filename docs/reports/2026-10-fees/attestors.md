# Attestor economics — adoption tier × YEC price × bond rule (D-Q-25, red flag 3)

Analytic, frozen data only (`ybcal fees --attestors`). 6 seated attestors (`mSelect` 4 + `kSlack` 2); the attestor share is `attestFeeBps` = 50 % of FEE-1, paid by the minter **on top of** the pool fee (AFEE-1), on mints only — owner redeems pay the pool alone, transfers and claims pay network fees. Under Option D the fee is dollars at `pMint`, so **every revenue figure below is price-invariant**; only the bond's dollar value (and so the return on it) moves with the YEC price. Cost of capital: 5%/yr on the bond's dollar value (policy `bond_opportunity_cost_apr`), stated separately from revenue throughout.

## 1. Adoption tiers and mint-size models

| Tier | mints/day | redeems/day | transfers/day | claims/day | YED outstanding | on top |
|---|---:|---:|---:|---:|---:|---|
| low | 2 | 1 | 10 | 0.2 | $50,000 | — |
| mid | 10 | 6 | 100 | 1 | $500,000 | — |
| high | 50 | 30 | 1,000 | 5 | $5,000,000 | — |
| whale | 10 | 6 | 100 | 1 | $500,000 | 10 × $100,000 mints a year (each redeemed once) |

low / mid / high are the policy's `adoption_scenarios` (mints, redeems, YED outstanding) with `pq_growth`'s transfers and claims; whale is mid plus ten $100k vaults a year. Mint-size models (no real size data): **supply-consistent** — the mean mint that keeps the tier's YED outstanding over 180-day mean lives ($139 in the low tier, $278 mid, $556 high; fee $1.39 / $2.78 / $5.56 per mint); **log-uniform to $100k** (`d24`) — mean mint $14,462, mean fee $37.51 per mint, dominated by the top decade. A $100k whale mint pays $145.

## 2. Revenue per seat (price-invariant)

| Tier | sizes | mints/yr | mean mint | fee/mint | whale fees $/yr | set $/yr at 50 % | **$/seat/month at 50 %** | $/seat/yr at 50 % | $/seat/month at 75 % | at 100 % |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| low | supply | 730 | $138.89 | $1.39 | $0.00 | $506.94 | **$7.04** | $84.49 | $10.56 | $14.08 |
| low | d24 | 730 | $14,462 | $37.51 | $0.00 | $13,692 | **$190.17** | $2,282 | $285.25 | $380.34 |
| mid | supply | 3,650 | $277.78 | $2.78 | $0.00 | $5,069 | **$70.41** | $844.91 | $105.61 | $140.82 |
| mid | d24 | 3,650 | $14,462 | $37.51 | $0.00 | $68,461 | **$950.85** | $11,410 | $1,426 | $1,902 |
| high | supply | 18,250 | $555.56 | $5.56 | $0.00 | $50,694 | **$704.09** | $8,449 | $1,056 | $1,408 |
| high | d24 | 18,250 | $14,462 | $37.51 | $0.00 | $342,304 | **$4,754** | $57,051 | $7,131 | $9,508 |
| whale | supply | 3,660 | $277.78 | $2.78 | $1,450 | $5,794 | **$80.48** | $965.74 | $120.72 | $160.96 |
| whale | d24 | 3,660 | $14,462 | $37.51 | $1,450 | $69,186 | **$960.91** | $11,531 | $1,441 | $1,922 |

The policy's attestor floor is $50/seat/month. Raising the share raises the minter's cost (the attestor output is on top of FEE-1, so 100 % doubles what a mint pays in protocol fee) and never changes the pool's cut; it scales every row linearly and changes no verdict: at the low tier 100 % is still $14.08/seat/month.

## 3. Return on bond (% per year, at the 50 % share)

Return = a seat's yearly revenue ÷ the bond's dollar value at the price shown. Bond rules: (a) **20,000 YEC** (today's `bondMin`), (b) **$8,000 pinned** at the registration price with the 2,000-YEC floor (D-Q-23a), and $2,000 pinned with the same floor. Bond value by price:

| Bond rule | $0.40 | $1.00 | $5.00 | $10.00 |
|---|---:|---:|---:|---:|
| $2,000 pinned | 5,000 YEC = $2,000 | 2,000 YEC = $2,000 | 2,000 YEC = $10,000 | 2,000 YEC = $20,000 |
| $8,000 pinned | 20,000 YEC = $8,000 | 8,000 YEC = $8,000 | 2,000 YEC = $10,000 | 2,000 YEC = $20,000 |
| 20,000 YEC | 20,000 YEC = $8,000 | 20,000 YEC = $20,000 | 20,000 YEC = $100,000 | 20,000 YEC = $200,000 |

The 2,000-YEC floor binds on the $2,000 bond above $1 and on the $8,000 bond above $4, so at $5 and $10 both pinned bonds are the floor ($10,000 / $20,000) and the two rules are the same bond.

| Tier | sizes | $/seat/yr | $2,000 pinned | $8,000 pinned | 20,000 YEC |
|---|---|---:|---|---|---|
| low | supply | $84.49 | 4.2% / 4.2% / 0.8% / 0.4% | 1.1% / 1.1% / 0.8% / 0.4% | 1.1% / 0.4% / 0.1% / 0.0% |
| low | d24 | $2,282 | **114.1%** / **114.1%** / **22.8%** / **11.4%** | **28.5%** / **28.5%** / **22.8%** / **11.4%** | **28.5%** / **11.4%** / 2.3% / 1.1% |
| mid | supply | $844.91 | **42.2%** / **42.2%** / **8.4%** / 4.2% | **10.6%** / **10.6%** / **8.4%** / 4.2% | **10.6%** / 4.2% / 0.8% / 0.4% |
| mid | d24 | $11,410 | **570.5%** / **570.5%** / **114.1%** / **57.1%** | **142.6%** / **142.6%** / **114.1%** / **57.1%** | **142.6%** / **57.1%** / **11.4%** / **5.7%** |
| high | supply | $8,449 | **422.5%** / **422.5%** / **84.5%** / **42.2%** | **105.6%** / **105.6%** / **84.5%** / **42.2%** | **105.6%** / **42.2%** / **8.4%** / 4.2% |
| high | d24 | $57,051 | **2853%** / **2853%** / **570.5%** / **285.3%** | **713.1%** / **713.1%** / **570.5%** / **285.3%** | **713.1%** / **285.3%** / **57.1%** / **28.5%** |
| whale | supply | $965.74 | **48.3%** / **48.3%** / **9.7%** / 4.8% | **12.1%** / **12.1%** / **9.7%** / 4.8% | **12.1%** / 4.8% / 1.0% / 0.5% |
| whale | d24 | $11,531 | **576.5%** / **576.5%** / **115.3%** / **57.7%** | **144.1%** / **144.1%** / **115.3%** / **57.7%** | **144.1%** / **57.7%** / **11.5%** / **5.8%** |

Cells: return at $0.40 / $1.00 / $5.00 / $10.00; **bold** clears the 5% cost of capital. The 20,000-YEC bond's return falls 25× from $0.40 to $10 at the same adoption; the pinned bond's falls only through its YEC floor (2.5× from $1 to $10).

## 4. Break-even adoption: mints/day for return ≥ 5%

Closed form: `mints/day = apr × bond × seats / (365 × fee per mint × share)`. Fee per mint: the low tier's supply-consistent $1.39 (conservative: that model's mean grows with the tier) and log-uniform-to-$100k $37.51. For reference: low 2, mid 10, high 50 mints a day.

| sizes | share | bond | mints/day at $0.40 | mints/day at $1.00 | mints/day at $5.00 | mints/day at $10.00 |
|---|---:|---|---:|---:|---:|---:|
| supply | 50% | $2,000 pinned | 2.4 | 2.4 | 11.8 | 23.7 |
| supply | 50% | $8,000 pinned | 9.5 | 9.5 | 11.8 | 23.7 |
| supply | 50% | 20,000 YEC | 9.5 | 23.7 | 118.4 | 236.7 |
| supply | 75% | $2,000 pinned | 1.6 | 1.6 | 7.9 | 15.8 |
| supply | 75% | $8,000 pinned | 6.3 | 6.3 | 7.9 | 15.8 |
| supply | 75% | 20,000 YEC | 6.3 | 15.8 | 78.9 | 157.8 |
| supply | 100% | $2,000 pinned | 1.2 | 1.2 | 5.9 | 11.8 |
| supply | 100% | $8,000 pinned | 4.7 | 4.7 | 5.9 | 11.8 |
| supply | 100% | 20,000 YEC | 4.7 | 11.8 | 59.2 | 118.4 |
| d24 | 50% | $2,000 pinned | 0.1 | 0.1 | 0.4 | 0.9 |
| d24 | 50% | $8,000 pinned | 0.4 | 0.4 | 0.4 | 0.9 |
| d24 | 50% | 20,000 YEC | 0.4 | 0.9 | 4.4 | 8.8 |
| d24 | 75% | $2,000 pinned | 0.1 | 0.1 | 0.3 | 0.6 |
| d24 | 75% | $8,000 pinned | 0.2 | 0.2 | 0.3 | 0.6 |
| d24 | 75% | 20,000 YEC | 0.2 | 0.6 | 2.9 | 5.8 |
| d24 | 100% | $2,000 pinned | 0.0 | 0.0 | 0.2 | 0.4 |
| d24 | 100% | $8,000 pinned | 0.2 | 0.2 | 0.2 | 0.4 |
| d24 | 100% | 20,000 YEC | 0.2 | 0.4 | 2.2 | 4.4 |

## 5. The slashing side: what a seat risks against what it earns

| YEC price | bond rule | bond | capture capital (7 seats) | D-Q-25 cap (15 % of supply × price) | capture ÷ cap | old base: one year of MINT-6 growth | capture ÷ old | bond/seat to meet D-RD-ATT-4 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| $0.40 | $2,000 pinned | $2,000 | $14,000 | $1,018,352 | 1.4% | $39,420 | 0.36× | $145,479 |
| $0.40 | $8,000 pinned | $8,000 | $56,000 | $1,018,352 | 5.5% | $39,420 | 1.42× | $145,479 |
| $0.40 | 20,000 YEC | $8,000 | $56,000 | $1,018,352 | 5.5% | $39,420 | 1.42× | $145,479 |
| $1.00 | $2,000 pinned | $2,000 | $14,000 | $2,545,880 | 0.5% | $98,550 | 0.14× | $363,697 |
| $1.00 | $8,000 pinned | $8,000 | $56,000 | $2,545,880 | 2.2% | $98,550 | 0.57× | $363,697 |
| $1.00 | 20,000 YEC | $20,000 | $140,000 | $2,545,880 | 5.5% | $98,550 | 1.42× | $363,697 |
| $5.00 | $2,000 pinned | $10,000 | $70,000 | $12,729,402 | 0.5% | $492,750 | 0.14× | $1,818,486 |
| $5.00 | $8,000 pinned | $10,000 | $70,000 | $12,729,402 | 0.5% | $492,750 | 0.14× | $1,818,486 |
| $5.00 | 20,000 YEC | $100,000 | $700,000 | $12,729,402 | 5.5% | $492,750 | 1.42× | $1,818,486 |
| $10.00 | $2,000 pinned | $20,000 | $140,000 | $25,458,804 | 0.5% | $985,500 | 0.14× | $3,636,972 |
| $10.00 | $8,000 pinned | $20,000 | $140,000 | $25,458,804 | 0.5% | $985,500 | 0.14× | $3,636,972 |
| $10.00 | 20,000 YEC | $200,000 | $1,400,000 | $25,458,804 | 5.5% | $985,500 | 1.42× | $3,636,972 |

**Judgement.** A seat risks its whole bond — burned on equivocation (two signatures over two spends of one outpoint; upgrade plan §3.6, EQV-1 for prices) or on a `SET_REMOVE burn=1` by 7 members — to earn $84.49/yr at the low tier and $844.91/yr at mid: the fee never pays for a seat's misbehaviour, so the stake-at-risk argument holds at any bond in this range. What the bond's *size* buys is the cost of capturing the quantile by seat splitting (§3.2: a set referenced by id can be slashed; C-4 / D-RD-ATT-4: 7 of 9 seats). That cost is $56,000 with the $8,000 bond and $14,000 with $2,000 at $0.40 — against a D-Q-25 cap of $1,018,352, i.e. 5.5% / 1.4% of what a captured price could mint. D-RD-ATT-4's yardstick (capture capital ≥ one year of cap growth) was met by the old issuance base (1.42× with $8,000) and **is not met by any affordable bond under D-Q-25** — it would take $145,479 a seat at $0.40. So the security argument the upgrade plan makes does not rest on the bond alone: it rests on H-1 (`pMint = min(xMint, aMint)`: a captured attestor set can raise the mint price only up to the pools' own price), the arming bar (H-2, seven matured distinct bonds), slashing, and the per-vault share limit. A lower bond weakens the one leg it does carry — the splitting cost — 4× (from $56,000 to $14,000) and the equivocation stake 4×, for a return-on-bond gain that still does not make the low tier pay (§3). A lower bond buys no revenue; it only changes the denominator.

## 6. Recommendation for launch

**Share 50 % (`attestFeeBps` 5,000), bond $8,000 pinned at the registration price with the 2,000-YEC floor.** Reasoning: (1) **revenue is set by adoption, not by bond size** — a seat's income is mints/day × fee × share ÷ 6 and contains no bond term; the bond only sets the hurdle, and the $8,000 bond's hurdle (9.5 mints/day at 50 %) is the mid tier, exactly where the Option D bands start paying the set anyway (§2); (2) the $2,000 bond lowers the hurdle to 2.4 mints/day but cuts the splitting cost to $14,000 for the whole set (§5), and the low tier still earns $7.04/seat/month either way; (3) 20,000 YEC is $8,000 today but $200,000 at $10, where even the high tier (supply-consistent sizes) returns under 5 % — the pin removes the price dependence that D-Q-23a already flagged; (4) a higher share is a module parameter the owner can raise later without touching the primitive, but at launch it only taxes minters (75 % adds a quarter to every mint's protocol fee) to move the low tier from $7.04 to $10.56 a month. At launch the seats are run the way the pools are at $25/pool/month: by operators who hold YEC and want the system to exist, not for the fee. The lever that pays attestors is mints per day; a renewal release can revisit the share once the real mint count is known.

