## 1. Supply and the cap (height 3,062,423, supply 16,972,536 YEC, subsidy 1.5625 YEC/block, next halving 3,960,000)

| YEC price | market cap | 15 % of it | $100k vaults | MINT-6 cap after 30 d | after 365 d | $100k vaults in year 1 | days to one $100k (A/B) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| $0.40 | $6,789,014 | $1,018,352 | 10 | $3,240 | $39,420 | 0.4 | 926 |
| $1.00 | $16,972,536 | $2,545,880 | 25 | $8,100 | $98,550 | 1.0 | 370 |
| $5.00 | $84,862,680 | $12,729,402 | 127 | $40,500 | $492,750 | 4.9 | 74 |
| $10.00 | $169,725,359 | $25,458,804 | 254 | $81,000 | $985,500 | 9.9 | 37 |
| $4,294.97 | $72,896,486,765 | $10,934,473,015 | 109,344 | $34,789,235 | $423,269,027 | 4,232.7 | 0 |

### Concentration (G3 claimant model against the frozen bid books)

| Case | vaults | debt each | one claim executes at p10 / p50 whole-book depth | one sale vs p10 daily volume | unexecutable share | one owner absent | one key lost |
|---|---:|---:|---|---:|---:|---:|---:|
| one vault at 25% of the cap | 1 | $254,588 | no / no | 12.73× | 100% | 100% | 100% |
| the same 25% over 100 vaults | 100 | $2,546 | yes / yes | 0.13× | 0% | 1% | 1% |
| one vault at 50% of the cap | 1 | $509,176 | no / no | 25.46× | 100% | 100% | 100% |
| the same 50% over 100 vaults | 100 | $5,092 | yes / yes | 0.25× | 0% | 1% | 1% |

Largest executable debt per depth band: 2pct_p10 $257.52, 2pct_p50 $800.40, 10pct_p10 $5,505, 10pct_p50 $15,298, 30pct_p10 $25,815, 30pct_p50 $34,675

## 2. Option D protocol fee by vault size

| Debt | fee / action | effective | mint total (pool + attestors) | round trip | early A / B / C (on debt) | early A / B / C today (on collateral) | claim loss ≤ | fee in YEC at $0.40 / $1 / $5 / $10 / $4,295 | today's fee at $0.40 / $1 / $5 / $10 / $4,295 |
|---:|---:|---:|---:|---:|---|---|---:|---|---|
| $100.00 | $1.00 | 1.000% | $1.50 | 2.500% | $5.00 / $2.50 / $1.00 | $15.00 / $10.00 / $5.00 | $25.00 | 2.5000 / 1.0000 / 0.2000 / 0.1000 / 0.0002 | $0.45 / $0.50 / $2.50 / $5.00 / $2,147 |
| $250.00 | $2.50 | 1.000% | $3.75 | 2.500% | $12.50 / $6.25 / $2.50 | $37.50 / $25.00 / $12.50 | $62.50 | 6.2500 / 2.5000 / 0.5000 / 0.2500 / 0.0006 | $1.12 / $1.12 / $2.50 / $5.00 / $2,147 |
| $500.00 | $5.00 | 1.000% | $7.50 | 2.500% | $25.00 / $12.50 / $5.00 | $75.00 / $50.00 / $25.00 | $125.00 | 12.5000 / 5.0000 / 1.0000 / 0.5000 / 0.0012 | $2.25 / $2.25 / $2.50 / $5.00 / $2,147 |
| $1,000 | $10.00 | 1.000% | $15.00 | 2.500% | $50.00 / $25.00 / $10.00 | $150.00 / $100.00 / $50.00 | $250.00 | 25.0000 / 10.0000 / 2.0000 / 1.0000 / 0.0023 | $4.50 / $4.50 / $4.50 / $5.00 / $2,147 |
| $2,500 | $17.50 | 0.700% | $26.25 | 1.750% | $125.00 / $62.50 / $25.00 | $375.00 / $250.00 / $125.00 | $625.00 | 43.7500 / 17.5000 / 3.5000 / 1.7500 / 0.0041 | $11.25 / $11.25 / $11.25 / $11.25 / $2,147 |
| $5,000 | $30.00 | 0.600% | $45.00 | 1.500% | $250.00 / $125.00 / $50.00 | $750.00 / $500.00 / $250.00 | $1,250 | 75.0000 / 30.0000 / 6.0000 / 3.0000 / 0.0070 | $22.50 / $22.50 / $22.50 / $22.50 / $2,147 |
| $10,000 | $55.00 | 0.550% | $82.50 | 1.375% | $500.00 / $250.00 / $100.00 | $1,500 / $1,000 / $500.00 | $2,500 | 137.5000 / 55.0000 / 11.0000 / 5.5000 / 0.0128 | $45.00 / $45.00 / $45.00 / $45.00 / $2,147 |
| $25,000 | $70.00 | 0.280% | $105.00 | 0.700% | $1,250 / $625.00 / $250.00 | $3,750 / $2,500 / $1,250 | $6,250 | 175.0000 / 70.0000 / 14.0000 / 7.0000 / 0.0163 | $112.50 / $112.50 / $112.50 / $112.50 / $2,147 |
| $50,000 | $95.00 | 0.190% | $142.50 | 0.475% | $2,500 / $1,250 / $500.00 | $7,500 / $5,000 / $2,500 | $12,500 | 237.5000 / 95.0000 / 19.0000 / 9.5000 / 0.0221 | $225.00 / $225.00 / $225.00 / $225.00 / $2,147 |
| $100,000 | $145.00 | 0.145% | $217.50 | 0.363% | $5,000 / $2,500 / $1,000 | $15,000 / $10,000 / $5,000 | $25,000 | 362.5000 / 145.0000 / 29.0000 / 14.5000 / 0.0338 | $450.00 / $450.00 / $450.00 / $450.00 / $2,147 |

### Revenue per year (FEE-1 to the pools, AFEE-1 to the seated attestors)

| Scenario | sizes | rule | YEC price | mints/yr | redeems/yr | mean debt | pools $/yr | attestors $/yr | $/pool/month | $/seat/month | bond cost $/month | seat ≥ bond | seat ≥ $50 | pool ≥ $50 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| low | supply | option_d | $0.40 | 730 | 365 | $138.89 | $1,521 | $506.94 | $25.35 | $7.04 | $33.33 | no | no | no |
| low | supply | today | $0.40 | 730 | 365 | $138.89 | $684.37 | $228.12 | $11.41 | $3.17 | $33.33 | no | no | no |
| low | supply | option_d | $1.00 | 730 | 365 | $138.89 | $1,521 | $506.94 | $25.35 | $7.04 | $83.33 | no | no | no |
| low | supply | today | $1.00 | 730 | 365 | $138.89 | $684.37 | $228.12 | $11.41 | $3.17 | $83.33 | no | no | no |
| low | supply | option_d | $5.00 | 730 | 365 | $138.89 | $1,521 | $506.94 | $25.35 | $7.04 | $416.67 | no | no | no |
| low | supply | today | $5.00 | 730 | 365 | $138.89 | $2,738 | $912.50 | $45.62 | $12.67 | $416.67 | no | no | no |
| low | supply | option_d | $10.00 | 730 | 365 | $138.89 | $1,521 | $506.94 | $25.35 | $7.04 | $833.33 | no | no | no |
| low | supply | today | $10.00 | 730 | 365 | $138.89 | $5,475 | $1,825 | $91.25 | $25.35 | $833.33 | no | no | yes |
| low | supply | option_d | $4,294.97 | 730 | 365 | $138.89 | $1,521 | $506.94 | $25.35 | $7.04 | $357,914 | no | no | no |
| low | supply | today | $4,294.97 | 730 | 365 | $138.89 | $2,351,495 | $783,832 | $39,192 | $10,887 | $357,914 | no | yes | yes |
| low | h12 | option_d | $0.40 | 730 | 365 | $745.60 | $7,172 | $2,391 | $119.53 | $33.20 | $33.33 | no | no | yes |
| low | h12 | today | $0.40 | 730 | 365 | $745.60 | $3,674 | $1,225 | $61.23 | $17.01 | $33.33 | no | no | yes |
| low | h12 | option_d | $1.00 | 730 | 365 | $745.60 | $7,172 | $2,391 | $119.53 | $33.20 | $83.33 | no | no | yes |
| low | h12 | today | $1.00 | 730 | 365 | $745.60 | $3,675 | $1,225 | $61.25 | $17.01 | $83.33 | no | no | yes |
| low | h12 | option_d | $5.00 | 730 | 365 | $745.60 | $7,172 | $2,391 | $119.53 | $33.20 | $416.67 | no | no | yes |
| low | h12 | today | $5.00 | 730 | 365 | $745.60 | $4,435 | $1,478 | $73.92 | $20.53 | $416.67 | no | no | yes |
| low | h12 | option_d | $10.00 | 730 | 365 | $745.60 | $7,172 | $2,391 | $119.53 | $33.20 | $833.33 | no | no | yes |
| low | h12 | today | $10.00 | 730 | 365 | $745.60 | $6,222 | $2,074 | $103.70 | $28.80 | $833.33 | no | no | yes |
| low | h12 | option_d | $4,294.97 | 730 | 365 | $745.60 | $7,172 | $2,391 | $119.53 | $33.20 | $357,914 | no | no | yes |
| low | h12 | today | $4,294.97 | 730 | 365 | $745.60 | $2,351,495 | $783,832 | $39,192 | $10,887 | $357,914 | no | yes | yes |
| low | d24 | option_d | $0.40 | 730 | 365 | $14,462 | $41,077 | $13,692 | $684.61 | $190.17 | $33.33 | yes | yes | yes |
| low | d24 | today | $0.40 | 730 | 365 | $14,462 | $71,262 | $23,754 | $1,188 | $329.91 | $33.33 | yes | yes | yes |
| low | d24 | option_d | $1.00 | 730 | 365 | $14,462 | $41,077 | $13,692 | $684.61 | $190.17 | $83.33 | yes | yes | yes |
| low | d24 | today | $1.00 | 730 | 365 | $14,462 | $71,262 | $23,754 | $1,188 | $329.92 | $83.33 | yes | yes | yes |
| low | d24 | option_d | $5.00 | 730 | 365 | $14,462 | $41,077 | $13,692 | $684.61 | $190.17 | $416.67 | no | yes | yes |
| low | d24 | today | $5.00 | 730 | 365 | $14,462 | $71,616 | $23,872 | $1,194 | $331.56 | $416.67 | no | yes | yes |
| low | d24 | option_d | $10.00 | 730 | 365 | $14,462 | $41,077 | $13,692 | $684.61 | $190.17 | $833.33 | no | yes | yes |
| low | d24 | today | $10.00 | 730 | 365 | $14,462 | $72,449 | $24,150 | $1,207 | $335.41 | $833.33 | no | yes | yes |
| low | d24 | option_d | $4,294.97 | 730 | 365 | $14,462 | $41,077 | $13,692 | $684.61 | $190.17 | $357,914 | no | yes | yes |
| low | d24 | today | $4,294.97 | 730 | 365 | $14,462 | $2,351,495 | $783,832 | $39,192 | $10,887 | $357,914 | no | yes | yes |
| mid | supply | option_d | $0.40 | 3,650 | 2,190 | $277.78 | $16,222 | $5,069 | $270.37 | $70.41 | $33.33 | yes | yes | yes |
| mid | supply | today | $0.40 | 3,650 | 2,190 | $277.78 | $7,300 | $2,281 | $121.67 | $31.68 | $33.33 | no | no | yes |
| mid | supply | option_d | $1.00 | 3,650 | 2,190 | $277.78 | $16,222 | $5,069 | $270.37 | $70.41 | $83.33 | no | yes | yes |
| mid | supply | today | $1.00 | 3,650 | 2,190 | $277.78 | $7,300 | $2,281 | $121.67 | $31.68 | $83.33 | no | no | yes |
| mid | supply | option_d | $5.00 | 3,650 | 2,190 | $277.78 | $16,222 | $5,069 | $270.37 | $70.41 | $416.67 | no | yes | yes |
| mid | supply | today | $5.00 | 3,650 | 2,190 | $277.78 | $14,600 | $4,562 | $243.33 | $63.37 | $416.67 | no | yes | yes |
| mid | supply | option_d | $10.00 | 3,650 | 2,190 | $277.78 | $16,222 | $5,069 | $270.37 | $70.41 | $833.33 | no | yes | yes |
| mid | supply | today | $10.00 | 3,650 | 2,190 | $277.78 | $29,200 | $9,125 | $486.67 | $126.74 | $833.33 | no | yes | yes |
| mid | supply | option_d | $4,294.97 | 3,650 | 2,190 | $277.78 | $16,222 | $5,069 | $270.37 | $70.41 | $357,914 | no | yes | yes |
| mid | supply | today | $4,294.97 | 3,650 | 2,190 | $277.78 | $12,541,305 | $3,919,158 | $209,022 | $54,433 | $357,914 | no | yes | yes |
| mid | h12 | option_d | $0.40 | 3,650 | 2,190 | $745.60 | $38,248 | $11,953 | $637.47 | $166.01 | $33.33 | yes | yes | yes |
| mid | h12 | today | $0.40 | 3,650 | 2,190 | $745.60 | $19,594 | $6,123 | $326.57 | $85.05 | $33.33 | yes | yes | yes |
| mid | h12 | option_d | $1.00 | 3,650 | 2,190 | $745.60 | $38,248 | $11,953 | $637.47 | $166.01 | $83.33 | yes | yes | yes |
| mid | h12 | today | $1.00 | 3,650 | 2,190 | $745.60 | $19,599 | $6,125 | $326.65 | $85.07 | $83.33 | yes | yes | yes |
| mid | h12 | option_d | $5.00 | 3,650 | 2,190 | $745.60 | $38,248 | $11,953 | $637.47 | $166.01 | $416.67 | no | yes | yes |
| mid | h12 | today | $5.00 | 3,650 | 2,190 | $745.60 | $23,653 | $7,392 | $394.22 | $102.66 | $416.67 | no | yes | yes |
| mid | h12 | option_d | $10.00 | 3,650 | 2,190 | $745.60 | $38,248 | $11,953 | $637.47 | $166.01 | $833.33 | no | yes | yes |
| mid | h12 | today | $10.00 | 3,650 | 2,190 | $745.60 | $33,183 | $10,370 | $553.05 | $144.02 | $833.33 | no | yes | yes |
| mid | h12 | option_d | $4,294.97 | 3,650 | 2,190 | $745.60 | $38,248 | $11,953 | $637.47 | $166.01 | $357,914 | no | yes | yes |
| mid | h12 | today | $4,294.97 | 3,650 | 2,190 | $745.60 | $12,541,305 | $3,919,158 | $209,022 | $54,433 | $357,914 | no | yes | yes |
| mid | d24 | option_d | $0.40 | 3,650 | 2,190 | $14,462 | $219,075 | $68,461 | $3,651 | $950.85 | $33.33 | yes | yes | yes |
| mid | d24 | today | $0.40 | 3,650 | 2,190 | $14,462 | $380,061 | $118,769 | $6,334 | $1,650 | $33.33 | yes | yes | yes |
| mid | d24 | option_d | $1.00 | 3,650 | 2,190 | $14,462 | $219,075 | $68,461 | $3,651 | $950.85 | $83.33 | yes | yes | yes |
| mid | d24 | today | $1.00 | 3,650 | 2,190 | $14,462 | $380,064 | $118,770 | $6,334 | $1,650 | $83.33 | yes | yes | yes |
| mid | d24 | option_d | $5.00 | 3,650 | 2,190 | $14,462 | $219,075 | $68,461 | $3,651 | $950.85 | $416.67 | yes | yes | yes |
| mid | d24 | today | $5.00 | 3,650 | 2,190 | $14,462 | $381,953 | $119,360 | $6,366 | $1,658 | $416.67 | yes | yes | yes |
| mid | d24 | option_d | $10.00 | 3,650 | 2,190 | $14,462 | $219,075 | $68,461 | $3,651 | $950.85 | $833.33 | yes | yes | yes |
| mid | d24 | today | $10.00 | 3,650 | 2,190 | $14,462 | $386,393 | $120,748 | $6,440 | $1,677 | $833.33 | yes | yes | yes |
| mid | d24 | option_d | $4,294.97 | 3,650 | 2,190 | $14,462 | $219,075 | $68,461 | $3,651 | $950.85 | $357,914 | no | yes | yes |
| mid | d24 | today | $4,294.97 | 3,650 | 2,190 | $14,462 | $12,541,305 | $3,919,158 | $209,022 | $54,433 | $357,914 | no | yes | yes |
| high | supply | option_d | $0.40 | 18,250 | 10,950 | $555.56 | $162,222 | $50,694 | $2,704 | $704.09 | $33.33 | yes | yes | yes |
| high | supply | today | $0.40 | 18,250 | 10,950 | $555.56 | $73,000 | $22,812 | $1,217 | $316.84 | $33.33 | yes | yes | yes |
| high | supply | option_d | $1.00 | 18,250 | 10,950 | $555.56 | $162,222 | $50,694 | $2,704 | $704.09 | $83.33 | yes | yes | yes |
| high | supply | today | $1.00 | 18,250 | 10,950 | $555.56 | $73,000 | $22,812 | $1,217 | $316.84 | $83.33 | yes | yes | yes |
| high | supply | option_d | $5.00 | 18,250 | 10,950 | $555.56 | $162,222 | $50,694 | $2,704 | $704.09 | $416.67 | yes | yes | yes |
| high | supply | today | $5.00 | 18,250 | 10,950 | $555.56 | $73,000 | $22,812 | $1,217 | $316.84 | $416.67 | no | yes | yes |
| high | supply | option_d | $10.00 | 18,250 | 10,950 | $555.56 | $162,222 | $50,694 | $2,704 | $704.09 | $833.33 | no | yes | yes |
| high | supply | today | $10.00 | 18,250 | 10,950 | $555.56 | $146,000 | $45,625 | $2,433 | $633.68 | $833.33 | no | yes | yes |
| high | supply | option_d | $4,294.97 | 18,250 | 10,950 | $555.56 | $162,222 | $50,694 | $2,704 | $704.09 | $357,914 | no | yes | yes |
| high | supply | today | $4,294.97 | 18,250 | 10,950 | $555.56 | $62,706,523 | $19,595,788 | $1,045,109 | $272,164 | $357,914 | no | yes | yes |
| high | h12 | option_d | $0.40 | 18,250 | 10,950 | $745.60 | $191,240 | $59,763 | $3,187 | $830.04 | $33.33 | yes | yes | yes |
| high | h12 | today | $0.40 | 18,250 | 10,950 | $745.60 | $97,972 | $30,616 | $1,633 | $425.23 | $33.33 | yes | yes | yes |
| high | h12 | option_d | $1.00 | 18,250 | 10,950 | $745.60 | $191,240 | $59,763 | $3,187 | $830.04 | $83.33 | yes | yes | yes |
| high | h12 | today | $1.00 | 18,250 | 10,950 | $745.60 | $97,996 | $30,624 | $1,633 | $425.33 | $83.33 | yes | yes | yes |
| high | h12 | option_d | $5.00 | 18,250 | 10,950 | $745.60 | $191,240 | $59,763 | $3,187 | $830.04 | $416.67 | yes | yes | yes |
| high | h12 | today | $5.00 | 18,250 | 10,950 | $745.60 | $118,265 | $36,958 | $1,971 | $513.30 | $416.67 | yes | yes | yes |
| high | h12 | option_d | $10.00 | 18,250 | 10,950 | $745.60 | $191,240 | $59,763 | $3,187 | $830.04 | $833.33 | no | yes | yes |
| high | h12 | today | $10.00 | 18,250 | 10,950 | $745.60 | $165,915 | $51,848 | $2,765 | $720.12 | $833.33 | no | yes | yes |
| high | h12 | option_d | $4,294.97 | 18,250 | 10,950 | $745.60 | $191,240 | $59,763 | $3,187 | $830.04 | $357,914 | no | yes | yes |
| high | h12 | today | $4,294.97 | 18,250 | 10,950 | $745.60 | $62,706,523 | $19,595,788 | $1,045,109 | $272,164 | $357,914 | no | yes | yes |
| high | d24 | option_d | $0.40 | 18,250 | 10,950 | $14,462 | $1,095,374 | $342,304 | $18,256 | $4,754 | $33.33 | yes | yes | yes |
| high | d24 | today | $0.40 | 18,250 | 10,950 | $14,462 | $1,900,307 | $593,846 | $31,672 | $8,248 | $33.33 | yes | yes | yes |
| high | d24 | option_d | $1.00 | 18,250 | 10,950 | $14,462 | $1,095,374 | $342,304 | $18,256 | $4,754 | $83.33 | yes | yes | yes |
| high | d24 | today | $1.00 | 18,250 | 10,950 | $14,462 | $1,900,318 | $593,849 | $31,672 | $8,248 | $83.33 | yes | yes | yes |
| high | d24 | option_d | $5.00 | 18,250 | 10,950 | $14,462 | $1,095,374 | $342,304 | $18,256 | $4,754 | $416.67 | yes | yes | yes |
| high | d24 | today | $5.00 | 18,250 | 10,950 | $14,462 | $1,909,763 | $596,801 | $31,829 | $8,289 | $416.67 | yes | yes | yes |
| high | d24 | option_d | $10.00 | 18,250 | 10,950 | $14,462 | $1,095,374 | $342,304 | $18,256 | $4,754 | $833.33 | yes | yes | yes |
| high | d24 | today | $10.00 | 18,250 | 10,950 | $14,462 | $1,931,967 | $603,740 | $32,199 | $8,385 | $833.33 | yes | yes | yes |
| high | d24 | option_d | $4,294.97 | 18,250 | 10,950 | $14,462 | $1,095,374 | $342,304 | $18,256 | $4,754 | $357,914 | no | yes | yes |
| high | d24 | today | $4,294.97 | 18,250 | 10,950 | $14,462 | $62,706,523 | $19,595,788 | $1,045,109 | $272,164 | $357,914 | no | yes | yes |
| whale | whale | option_d | $0.40 | 10 | 10 | $100,000 | $2,900 | $725.00 | $48.33 | $10.07 | $33.33 | no | no | no |
| whale | whale | today | $0.40 | 10 | 10 | $100,000 | $9,000 | $2,250 | $150.00 | $31.25 | $33.33 | no | no | yes |
| whale | whale | option_d | $1.00 | 10 | 10 | $100,000 | $2,900 | $725.00 | $48.33 | $10.07 | $83.33 | no | no | no |
| whale | whale | today | $1.00 | 10 | 10 | $100,000 | $9,000 | $2,250 | $150.00 | $31.25 | $83.33 | no | no | yes |
| whale | whale | option_d | $5.00 | 10 | 10 | $100,000 | $2,900 | $725.00 | $48.33 | $10.07 | $416.67 | no | no | no |
| whale | whale | today | $5.00 | 10 | 10 | $100,000 | $9,000 | $2,250 | $150.00 | $31.25 | $416.67 | no | no | yes |
| whale | whale | option_d | $10.00 | 10 | 10 | $100,000 | $2,900 | $725.00 | $48.33 | $10.07 | $833.33 | no | no | no |
| whale | whale | today | $10.00 | 10 | 10 | $100,000 | $9,000 | $2,250 | $150.00 | $31.25 | $833.33 | no | no | yes |
| whale | whale | option_d | $4,294.97 | 10 | 10 | $100,000 | $2,900 | $725.00 | $48.33 | $10.07 | $357,914 | no | no | no |
| whale | whale | today | $4,294.97 | 10 | 10 | $100,000 | $42,950 | $10,737 | $715.83 | $149.13 | $357,914 | no | yes | yes |

## 3. Network fee per path ($/KB targets, floor 1× relay, cap -maxtxfee 0.1 YEC)


**as decided, variant `pq_p2pkh`**

| Path | bytes | target | $ at target | cap binds below | parity fails above | pq m=50 zat | zat (bound) at $0.40 | zat (bound) at $1.00 | zat (bound) at $5.00 | zat (bound) at $10.00 | zat (bound) at $4,294.97 | zat/byte at $0.40 … $4,295 | ≥ P2PKH 4.08 zat/B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| transfer | 453 | $0.02/KB | $0.009 | $0.09 | $490 | 1,000 | 2,265,000 (target) | 906,000 (target) | 181,200 (target) | 90,600 (target) | 210 (target) | 5,000.0 … 0.5 | y / y / y / y / N |
| residual_sweep | 8,044 | $0.02/KB | $0.161 | $1.61 | $490 | 40,220 | 10,000,000 (cap) | 10,000,000 (cap) | 3,217,599 (target) | 1,608,799 (target) | 3,745 (target) | 1,243.2 … 0.5 | y / y / y / y / N |
| mint | 1,018 | $0.05/KB | $0.051 | $0.51 | $1,225 | 1,000 | 10,000,000 (cap) | 5,090,000 (target) | 1,018,000 (target) | 509,000 (target) | 1,185 (target) | 9,823.2 … 1.2 | y / y / y / y / N |
| redeem | 8,249 | $0.05/KB | $0.412 | $4.12 | $1,225 | 41,245 | 10,000,000 (cap) | 10,000,000 (cap) | 8,249,000 (target) | 4,124,500 (target) | 9,603 (target) | 1,212.3 … 1.2 | y / y / y / y / N |
| claim | 1,321 | $0.05/KB | $0.066 | $0.66 | $1,225 | 1,000 | 10,000,000 (cap) | 6,605,000 (target) | 1,321,000 (target) | 660,500 (target) | 1,537 (target) | 7,570.0 … 1.2 | y / y / y / y / N |
| bridge_recover | 8,045 | $0.05/KB | $0.402 | $4.02 | $1,225 | 40,225 | 10,000,000 (cap) | 10,000,000 (cap) | 8,044,999 (target) | 4,022,499 (target) | 9,365 (target) | 1,243.0 … 1.2 | y / y / y / y / N |

**as decided, variant `falcon`**

| Path | bytes | target | $ at target | cap binds below | parity fails above | pq m=50 zat | zat (bound) at $0.40 | zat (bound) at $1.00 | zat (bound) at $5.00 | zat (bound) at $10.00 | zat (bound) at $4,294.97 | zat/byte at $0.40 … $4,295 | ≥ P2PKH 4.08 zat/B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| transfer | 1,945 | $0.02/KB | $0.039 | $0.39 | $490 | 9,725 | 9,724,999 (target) | 3,889,999 (target) | 778,000 (target) | 389,000 (target) | 905 (target) | 5,000.0 … 0.5 | y / y / y / y / N |
| residual_sweep | 8,044 | $0.02/KB | $0.161 | $1.61 | $490 | 40,220 | 10,000,000 (cap) | 10,000,000 (cap) | 3,217,599 (target) | 1,608,799 (target) | 3,745 (target) | 1,243.2 … 0.5 | y / y / y / y / N |
| mint | 1,028 | $0.05/KB | $0.051 | $0.51 | $1,225 | 1,000 | 10,000,000 (cap) | 5,140,000 (target) | 1,028,000 (target) | 514,000 (target) | 1,196 (target) | 9,727.6 … 1.2 | y / y / y / y / N |
| redeem | 9,721 | $0.05/KB | $0.486 | $4.86 | $1,225 | 48,605 | 10,000,000 (cap) | 10,000,000 (cap) | 9,721,000 (target) | 4,860,500 (target) | 11,316 (target) | 1,028.7 … 1.2 | y / y / y / y / N |
| claim | 2,793 | $0.05/KB | $0.140 | $1.40 | $1,225 | 13,965 | 10,000,000 (cap) | 10,000,000 (cap) | 2,793,000 (target) | 1,396,500 (target) | 3,251 (target) | 3,580.4 … 1.2 | y / y / y / y / N |
| bridge_recover | 8,045 | $0.05/KB | $0.402 | $4.02 | $1,225 | 40,225 | 10,000,000 (cap) | 10,000,000 (cap) | 8,044,999 (target) | 4,022,499 (target) | 9,365 (target) | 1,243.0 … 1.2 | y / y / y / y / N |

**recommended: floor 50× relay, cap max($2, 0.1 YEC), variant `pq_p2pkh`**

| Path | bytes | target | $ at target | cap binds below | parity fails above | pq m=50 zat | zat (bound) at $0.40 | zat (bound) at $1.00 | zat (bound) at $5.00 | zat (bound) at $10.00 | zat (bound) at $4,294.97 | zat/byte at $0.40 … $4,295 | ≥ P2PKH 4.08 zat/B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| transfer | 453 | $0.02/KB | $0.009 | $0.00 | $490 | 1,000 | 2,265,000 (target) | 906,000 (target) | 181,200 (target) | 90,600 (target) | 2,265 (floor) | 5,000.0 … 5.0 | y / y / y / y / y |
| residual_sweep | 8,044 | $0.02/KB | $0.161 | $0.00 | $490 | 40,220 | 40,219,999 (target) | 16,088,000 (target) | 3,217,599 (target) | 1,608,799 (target) | 40,220 (floor) | 5,000.0 … 5.0 | y / y / y / y / y |
| mint | 1,018 | $0.05/KB | $0.051 | $0.00 | $1,225 | 1,000 | 12,725,000 (target) | 5,090,000 (target) | 1,018,000 (target) | 509,000 (target) | 5,090 (floor) | 12,500.0 … 5.0 | y / y / y / y / y |
| redeem | 8,249 | $0.05/KB | $0.412 | $0.00 | $1,225 | 41,245 | 103,112,500 (target) | 41,245,000 (target) | 8,249,000 (target) | 4,124,500 (target) | 41,245 (floor) | 12,500.0 … 5.0 | y / y / y / y / y |
| claim | 1,321 | $0.05/KB | $0.066 | $0.00 | $1,225 | 1,000 | 16,512,500 (target) | 6,605,000 (target) | 1,321,000 (target) | 660,500 (target) | 6,605 (floor) | 12,500.0 … 5.0 | y / y / y / y / y |
| bridge_recover | 8,045 | $0.05/KB | $0.402 | $0.00 | $1,225 | 40,225 | 100,562,500 (target) | 40,225,000 (target) | 8,044,999 (target) | 4,022,499 (target) | 40,225 (floor) | 12,500.0 … 5.0 | y / y / y / y / y |

**recommended: floor 50× relay, cap max($2, 0.1 YEC), variant `falcon`**

| Path | bytes | target | $ at target | cap binds below | parity fails above | pq m=50 zat | zat (bound) at $0.40 | zat (bound) at $1.00 | zat (bound) at $5.00 | zat (bound) at $10.00 | zat (bound) at $4,294.97 | zat/byte at $0.40 … $4,295 | ≥ P2PKH 4.08 zat/B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| transfer | 1,945 | $0.02/KB | $0.039 | $0.00 | $490 | 9,725 | 9,724,999 (target) | 3,889,999 (target) | 778,000 (target) | 389,000 (target) | 9,725 (floor) | 5,000.0 … 5.0 | y / y / y / y / y |
| residual_sweep | 8,044 | $0.02/KB | $0.161 | $0.00 | $490 | 40,220 | 40,219,999 (target) | 16,088,000 (target) | 3,217,599 (target) | 1,608,799 (target) | 40,220 (floor) | 5,000.0 … 5.0 | y / y / y / y / y |
| mint | 1,028 | $0.05/KB | $0.051 | $0.00 | $1,225 | 1,000 | 12,850,000 (target) | 5,140,000 (target) | 1,028,000 (target) | 514,000 (target) | 5,140 (floor) | 12,500.0 … 5.0 | y / y / y / y / y |
| redeem | 9,721 | $0.05/KB | $0.486 | $0.00 | $1,225 | 48,605 | 121,512,500 (target) | 48,605,000 (target) | 9,721,000 (target) | 4,860,500 (target) | 48,605 (floor) | 12,500.0 … 5.0 | y / y / y / y / y |
| claim | 2,793 | $0.05/KB | $0.140 | $0.00 | $1,225 | 13,965 | 34,912,500 (target) | 13,965,000 (target) | 2,793,000 (target) | 1,396,500 (target) | 13,965 (floor) | 12,500.0 … 5.0 | y / y / y / y / y |
| bridge_recover | 8,045 | $0.05/KB | $0.402 | $0.00 | $1,225 | 40,225 | 100,562,500 (target) | 40,225,000 (target) | 8,044,999 (target) | 4,022,499 (target) | 40,225 (floor) | 12,500.0 … 5.0 | y / y / y / y / y |

## 4. Robustness: a ±70 % move between mint ($0.40) and redeem, $1,000 class-A vault

| shock | price now | fee zat (fixed at pMint) | fee $ now | early A $ now | $1 floor now | collateral ratio | claimable |
|---:|---:|---:|---:|---:|---:|---:|---|
| -70% | $0.120 | 2,500,000,000 | $3.00 | $15.00 | $0.30 | 90% | yes |
| +0% | $0.400 | 2,500,000,000 | $10.00 | $50.00 | $1.00 | 300% | no |
| +70% | $0.680 | 2,500,000,000 | $17.00 | $85.00 | $1.70 | 510% | no |

### YEC-denominated constants restated in dollars

| constant | today (YEC) | today in $ at $0.40 / $1 / $5 / $10 / $4,295 | proposed $ | floor YEC | proposed in YEC at the same prices |
|---|---:|---|---:|---:|---|
| `bondMin` | 20,000 | $8,000 / $20,000 / $100,000 / $200,000 / $85,899,346 | $8,000 | 2,000.0 | 20,000.0000 / 8,000.0000 / 2,000.0000 / 2,000.0000 / 2,000.0000 |

  G8's bond_secure compares capture capital with the YED cap, both in dollars at the same price, so a dollar bond keeps the ratio; converted once at the attested price of the registration (REG-A1) and stored as the bond's YEC amount; the 2,000 YEC floor keeps a seat non-trivial if YEC ever trades near PRICE_MIN.

| `residualMinZat` | 0.001 | $0.00 / $0.00 / $0.01 / $0.01 / $4.29 | $0.48 | 0.001 | 1.2000 / 0.4800 / 0.0960 / 0.0480 / 0.0010 |

  G9's rule (residual ≥ dust_spend_multiple = 3 × the cost of spending it): the owner's residual is a PQPKH(owner) output swept by an SLH-DSA spend of 8,044 bytes, $0.161 at the PQ target; converted at pMint like the fees (the vault already stores it); the 100,000-zat floor is today's value.


## 5. Sensitivity (±50 % per knob)

| knob | × | rate $100 | $1k | $10k | $100k | whale round trip | low (supply sizes): $/seat/month at $0.40 | $/pool/month | SLH redeem cap binds below | SLH sweep cap binds below | yb parity fails above | pq parity fails above |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| base | 1 | 1.00% | 1.00% | 0.550% | 0.145% | $362.50 | $7.04 | $25.35 | $4.12 | $1.61 | $1,225 | $490 |
| b0 | 0.5 | 1.00% | 0.50% | 0.500% | 0.140% | $350.00 | $5.07 | $18.25 | $4.12 | $1.61 | $1,225 | $490 |
| b0 | 1.5 | 1.50% | 1.50% | 0.600% | 0.150% | $375.00 | $10.56 | $38.02 | $4.12 | $1.61 | $1,225 | $490 |
| b1 | 0.5 | 1.00% | 1.00% | 0.325% | 0.122% | $306.25 | $7.04 | $25.35 | $4.12 | $1.61 | $1,225 | $490 |
| b1 | 1.5 | 1.00% | 1.00% | 0.775% | 0.168% | $418.75 | $7.04 | $25.35 | $4.12 | $1.61 | $1,225 | $490 |
| b2 | 0.5 | 1.00% | 1.00% | 0.550% | 0.100% | $250.00 | $7.04 | $25.35 | $4.12 | $1.61 | $1,225 | $490 |
| b2 | 1.5 | 1.00% | 1.00% | 0.550% | 0.190% | $475.00 | $7.04 | $25.35 | $4.12 | $1.61 | $1,225 | $490 |
| pq | 0.5 | 1.00% | 1.00% | 0.550% | 0.145% | $362.50 | $7.04 | $25.35 | $4.12 | $0.80 | $1,225 | $245 |
| pq | 1.5 | 1.00% | 1.00% | 0.550% | 0.145% | $362.50 | $7.04 | $25.35 | $4.12 | $2.41 | $1,225 | $735 |
| yb | 0.5 | 1.00% | 1.00% | 0.550% | 0.145% | $362.50 | $7.04 | $25.35 | $2.06 | $1.61 | $612 | $490 |
| yb | 1.5 | 1.00% | 1.00% | 0.550% | 0.145% | $362.50 | $7.04 | $25.35 | $6.19 | $1.61 | $1,838 | $490 |

## Recommendation

Bands ((1000.0, 0.01), (10000.0, 0.005), (inf, 0.001)), floor $1/action, early (500, 250, 100) bps of the debt; network $0.02/KB PQ and $0.05/KB vault/Yellowback, floor 5,000.0 zat/KB, cap max($2 at the attested price, 0.1 YEC); maxVaultShareBps 1,000 of the cap with a $10,000 floor; bondMin $8,000 (floor 2,000 YEC); residualMin $0.48 at pMint (floor 100,000 zat).

- Bands as decided: effective rate 1.000% at $100, 1.000% at $1,000, 0.550% at $10,000, 0.145% at $100,000; a $100k vault's round trip (mint with the attestor share + redeem) is $362 against $1,125 under today's rule at $0.40 (0.15 % of a 300 % collateral at mint and redeem, plus the attestor share); a $100 vault pays $2.50 against $1.12. Flat in dollars at every price, where today's rule reaches $2,147 per action at the ceiling.
- Marginal bands never reward splitting a vault (ten $10k vaults pay $550 where one $100k pays $145); the $1 floor equals the 1 % band at minMint, so it never binds above $100.
- Revenue at $0.40, supply-consistent sizing (mean mint $139 in the low case): $1,521/yr to pools ($25/pool/month, floor $50) and $507/yr to the 6 seats ($7.04/seat/month against the bond's $33/month opportunity cost and the policy's $50 floor; today's rule gives $3.17); mid case $70/seat/month. Log-uniform sizing to $2,500 (H-12) gives $33/seat/month in the low case, to $100k $951 in the mid case; ten whales a year add $725/yr. The bands pay the attestor set only from the mid case on; the adequacy judgement is the owner's (the study reports the bands' yield, as asked).
- Early fee on the debt: class A 5 % of the debt is a fifth of the 25 % the owner can lose to an in-term claim (θ = 125 %), where the collateral rule charged 15 % of the debt at 300 %: redeeming early beats being claimed by a wide margin in every class, so no owner is pushed toward the claim path; and the fee no longer penalises over-collateralising. No new gaming path: the owner cannot choose pMint, a claim needs the price to fall under θ, and the debt is fixed at mint.
- Network fee: keep the $/KB targets but set the floor at 41× relay (P2PKH parity, the pq study's m = 50 → 5,000 zat/KB) instead of 1×: above $1,225/YEC the $0.05/KB target pays fewer zat per byte than a P2PKH payment, and the floor keeps the miners' per-byte income at parity there.
- -maxtxfee 0.1 YEC binds on the 8,249-byte SLH redeem below $4.12/YEC — at today's $0.40 the target is 1.03 YEC, ten times the cap. A fee capped at 0.1 YEC still pays 1,212 zat/byte, 300× parity, so the cap does no harm to relay; but the dollar target is then fiction at any price under $4. Wallet default: a dollar cap, max($2 at the attested price, 0.1 YEC), for the vault / Yellowback builders only (policy, not consensus); $2 covers every path's target up to a 40 KB transaction and is 46,566 zat at the ceiling.
- Supply cap: D-Q-25's shipped base is the circulating supply (16,972,537.5 YEC at height 3,062,423), not coinbase issued since the start height. The $3,240 / $39,420 figures are that retired issuance base and do not describe the rule. On the circulating base, MINT-6b at the unchanged maxVaultShareBps 1,000 (floor $10,000) is $101,835 at $0.40, above maxMint $100,000, so the share does not bound one vault there: the ceiling is maxMint, and the share reaches it near $0.393 ($100,002) and exceeds the $34,675 p50 executable claim above about $0.136 ($34,675). The owner ruling is absent (W4-K-2); the node constant was not changed. Class C's base equals recapRatioBps, so the aggregate cap never refuses a class C mint.
- Concentration: a $100k debt's claim must sell $125k of YEC into a bid book of $4,982 (whole visible book, p50) — the claimant model says no claim executes above a debt of $34,675 at that depth ($258 in G3's own 2 % band at p10), so a whale vault that goes under θ is bad debt until the market deepens, and one owner's absence or lost key puts the whole position in the claim path; 100 vaults of the same total are each executable and expose 1 % per owner. The shipped maxVaultShareBps stays 1,000 of the MINT-6 cap with a $10,000 floor: on the circulating base that share does not bound one vault at a realistic price. No owner ruling has replaced it (W4-K-2). $100,000 is 3× the largest claim the frozen books can absorb, so D-Q-24's maxMint is a depth bet, not a parameter the data supports.
- bondMin and residualMinZat follow the fee pattern: $8,000 at the registration price (floor 2,000 YEC) and $0.48 at pMint (floor 100,000 zat).
