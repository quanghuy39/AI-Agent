"""Bo kiem chung sau moi buoc: ham thuan, khong doc R2, khong ghi Ledger.

Moi ham tra ve {"name", "status": "pass"|"flag", "reason", "details"}; to_ledger_check doi sang Check cua so ket qua.
"""

from validators.join import join_check, normalize_keys
from validators.ranges import range_check
from validators.result import make_result, to_ledger_check
from validators.rows import row_count_check
from validators.sums import sum_consistency

__all__ = ["make_result", "to_ledger_check", "row_count_check", "join_check", "normalize_keys",
           "sum_consistency", "range_check"]
