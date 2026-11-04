import assert from 'node:assert/strict';
import test from 'node:test';
import { exportTransactionsCsv } from '../src/utils/exportTransactionsCsv.ts';

test('exports exact amounts, optional clock times and neutralized spreadsheet formulas', () => {
  const csv = exportTransactionsCsv([
    {
      id: 'one',
      version: 1,
      kind: 'expense',
      amount: { amount: '35.1250', currency: 'VND' },
      description: '=HYPERLINK("https://example.test")',
      transactionDate: '2026-11-03',
      localTime: null,
      dayPeriod: 'morning',
      timezone: 'Asia/Ho_Chi_Minh',
      category: 'Food',
      isDeleted: false,
    },
    {
      id: 'two',
      version: 1,
      kind: 'income',
      amount: { amount: '900', currency: 'VND' },
      description: 'Lương',
      transactionDate: '2026-11-02',
      localTime: '14:30',
      dayPeriod: 'afternoon',
      timezone: 'Asia/Ho_Chi_Minh',
      category: 'Salary',
      isDeleted: false,
    },
  ]);
  assert.ok(csv.startsWith('\uFEFF'));
  assert.match(csv, /"2026-11-03","","morning","expense","35.1250"/);
  assert.match(csv, /"'=HYPERLINK\(""https:\/\/example.test""\)"/);
  assert.match(csv, /"2026-11-02","14:30","afternoon","income","900"/);
});
