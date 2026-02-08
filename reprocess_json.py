#!/usr/bin/env python3
"""
reprocess_json.py  –  Fix multi-value fields in an existing stock_data.json

Usage:
    python reprocess_json.py stock_data.json stock_data_fixed.json

This applies the same split logic as the updated scraper to an already-scraped
JSON file, producing a clean version with separate keys for each value.
"""

import json
import sys
from scraper import split_multi_value_fields


def reprocess(input_path='stock_data.json', output_path='stock_data_fixed.json'):
    with open(input_path, 'r') as f:
        data = json.load(f)

    stocks = data.get('stocks', [])
    fixed = []
    for stock in stocks:
        fixed.append(split_multi_value_fields(stock))

    data['stocks'] = fixed

    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2, default=str)

    # Show before/after for the first stock that has the affected fields
    sample = None
    for s in stocks:
        if '52w_high' in s and isinstance(s['52w_high'], str):
            sample = s
            break
    if sample:
        ticker = sample['ticker']
        fixed_sample = split_multi_value_fields(sample)
        print(f"\n{'='*60}")
        print(f" BEFORE / AFTER  –  {ticker}")
        print(f"{'='*60}")

        field_pairs = [
            ('52w_high',          ['52w_high', '52w_high_pct']),
            ('52w_low',           ['52w_low', '52w_low_pct']),
            ('volatility',        ['volatility_week', 'volatility_month']),
            ('eps_past_3_5y',     ['eps_past_3y', 'eps_past_5y']),
            ('sales_past_3_5y',   ['sales_past_3y', 'sales_past_5y']),
            ('dividend_gr._3_5y', ['dividend_gr_3y', 'dividend_gr_5y']),
            ('eps_sales_surpr.',  ['eps_surprise', 'sales_surprise']),
            ('dividend_ttm',      ['dividend_ttm', 'dividend_yield']),
            ('dividend_est.',     ['dividend_est', 'dividend_yield_est']),
        ]

        for old_key, new_keys in field_pairs:
            old_val = sample.get(old_key, '(not present)')
            print(f"\n  BEFORE  {old_key:25s} = {old_val!r}")
            for nk in new_keys:
                new_val = fixed_sample.get(nk, '(not present)')
                print(f"  AFTER   {nk:25s} = {new_val!r}")

    print(f"\n✓ Wrote {len(fixed)} stocks to {output_path}")


if __name__ == "__main__":
    input_path = sys.argv[1] if len(sys.argv) > 1 else 'stock_data.json'
    output_path = sys.argv[2] if len(sys.argv) > 2 else 'stock_data_fixed.json'
    reprocess(input_path, output_path)