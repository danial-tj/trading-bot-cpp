import assert from 'node:assert/strict';
import {validDate,dateRangeError} from '../service/static/date-fields.js';

for(const date of ['2024-02-29','2026-10-06','1900-01-01'])assert.equal(validDate(date),true,date);
for(const date of ['2025-02-29','1900-02-29','2026-13-01','2026-04-31','2026-00-01','2026-01-00','2026-1-1','01/01/2026'])assert.equal(validDate(date),false,date);
assert.equal(dateRangeError('',''),null);
assert.equal(dateRangeError('2026-03-01',''),null);
assert.equal(dateRangeError('','2026-03-01'),null);
assert.equal(dateRangeError('2026-03-01','2026-03-01'),null);
assert.equal(dateRangeError('2026-03-02','2026-03-01').field,'end');
assert.equal(dateRangeError('','2026-03-01',{required:true}).field,'start');
assert.equal(dateRangeError('2024-01-01','2024-12-31',{maxDays:366}),null);
assert.equal(dateRangeError('2024-01-01','2025-01-01',{maxDays:366}).field,'end');
console.log('Date checks passed: Gregorian dates, leap years, blank bounds, order and inclusive range limits.');
