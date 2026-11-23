# New zones check

Checked 2026-11-23 14:28 UTC.


## Ireland (SEM) on ENTSO-E

Prices, first days of 2021: 73 points, 2020-12-31 23:00:00+00:00 to 2021-01-03 23:00:00+00:00, step 0 days 01:00:00
Daily baseload: 2021-01-01: 64.70, 2021-01-02: 77.96, 2021-01-03: 66.62
Prices, last 3 days and tomorrow: 120 points, 2026-11-19 23:00:00+00:00 to 2026-11-24 22:00:00+00:00, step 0 days 01:00:00
Daily baseload: 2026-11-20: 279.83, 2026-11-21: 218.17, 2026-11-22: 205.34, 2026-11-23: 233.01, 2026-11-24: 286.64
load forecast: NoMatchingDataError: 
wind/solar forecast: 24 rows; columns ['Wind Onshore']
generation: 46 rows; columns [('Fossil Gas', 'Actual Aggregated'), ('Fossil Hard coal', 'Actual Aggregated'), ('Fossil Oil', 'Actual Aggregated'), ('Fossil Peat', 'Actual Aggregated'), ('Hydro Pumped Storage', 'Actual Aggregated'), ('Hydro Run-of-river and pondage', 'Actual Aggregated'), ('Solar', 'Actual Aggregated'), ('Wind Onshore', 'Actual Aggregated'), ('Other', 'Actual Aggregated'), ('Fossil Gas', 'Actual Consumption'), ('Fossil Hard coal', 'Actual Consumption'), ('Fossil Oil', 'Actual Consumption')]
installed capacity: NoMatchingDataError: 

## Great Britain: LCCC IMRP actuals

Dataset metadata_modified 2026-11-23T03:12:14.095002, 3 resources
- d0e58993-d8b2-4ab5-90e0-518b1e7b2f1e | IMRP actuals | JSON | datastore True | created 2025-12-08T07:15:02.072305 | modified 2025-12-15T11:46:52.036323 | https://dp.lowcarboncontracts.uk/datastore/dump/d0e58993-d8b2-4ab5-90e0-518b1e7b2f1e
- 866e6a4e-86c7-411e-9464-2ac3ad56ae35 | IMRP actuals | CSV | datastore False | created 2025-12-08T07:16:21.003706 | modified 2026-11-23T03:12:14.084065 | https://dp.lowcarboncontracts.uk/dataset/19f1ebee-93b7-4ef4-9465-bba50fa4ad06/resource/866e6a4e-86c7-411e-9464-2ac3ad56ae35/download/imrp_actuals.csv
- c296e070-4bc8-449b-abab-6bdfee831d88 | IMRP actuals - Data Type Definitions | CSV | datastore False | created 2025-12-08T07:16:21.634559 | modified 2025-12-08T07:16:21.604987 | https://dp.lowcarboncontracts.uk/dataset/19f1ebee-93b7-4ef4-9465-bba50fa4ad06/resource/c296e070-4bc8-449b-abab-6bdfee831d88/download/imrp-actuals-data-type-definitions.csv
ERROR HTTPError: HTTP Error 403: Forbidden
```
py", line 39, in section
    fn()
  File "/home/runner/work/spotrank/spotrank/scripts/new_zones_check.py", line 77, in lccc
    raw, hdr = get(r["url"], timeout=120)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/runner/work/spotrank/spotrank/scripts/new_zones_check.py", line 32, in get
    with urllib.request.urlopen(req, timeout=timeout) as r:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/urllib/request.py", line 215, in urlopen
    return opener.open(url, data, timeout)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/urllib/request.py", line 521, in open
    response = meth(req, response)
               ^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/urllib/request.py", line 630, in http_response
    response = self.parent.error(
               ^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/urllib/request.py", line 559, in error
    return self._call_chain(*args)
           ^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/urllib/request.py", line 492, in _call_chain
    result = func(*args)
             ^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/urllib/request.py", line 639, in http_error_default
    raise HTTPError(req.full_url, code, msg, hdrs, fp)
urllib.error.HTTPError: HTTP Error 403: Forbidden

```

## ECB exchange rate

ECB EUR/GBP (GBP per EUR): 2026-11-16: 0.8345, 2026-11-17: 0.83305, 2026-11-18: 0.8329, 2026-11-19: 0.83581, 2026-11-20: 0.83735
