# New zones check

Checked 2026-11-23 15:39 UTC.


## Great Britain: NESO, N2EX day-ahead prices

package_show failed: the JSON object must be str, bytes or bytearray, not tuple
- 4f27eea5-7038-4f73-9740-e3e4ad47c26a | N2EX GB Day-Ahead Price | datastore True | modified None
ERROR TypeError: the JSON object must be str, bytes or bytearray, not tuple
```
Traceback (most recent call last):
  File "/home/runner/work/spotrank/spotrank/scripts/new_zones_check.py", line 39, in section
    fn()
  File "/home/runner/work/spotrank/spotrank/scripts/new_zones_check.py", line 133, in neso
    res = json.loads(get(f"{api}/datastore_search?resource_id={rid}&limit=3"))["result"]
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/json/__init__.py", line 339, in loads
    raise TypeError(f'the JSON object must be str, bytes or bytearray, '
TypeError: the JSON object must be str, bytes or bytearray, not tuple

```
