"""超级电脑（docs/SUPERCOMPUTER.md）：几位编程助手平级、没有固定主管，按分数派活。

- ``scoring``：擅长 + 今天剩余次数 + 忙闲 → 分数；拆分、每件小活、合并都按分数挑人。
- ``ledger``：每天派了几件、谁在冷却（撞到订阅限额后）、界面上改过的每天次数。
- ``runner``：在电脑上跑官方命令行，任务正文从标准输入给，超时就连同子进程一起结束。
- ``engine``：拆分 → 并行干 → 合并；撞限额的换下一位；记录写在 data/super/。
- ``butler``：管家——定时活、排队、自治闸门（``gate``）、日报、哈希链审计（``audit``）。

只在用户交活时干活；不读写圆桌的数据库。dsh 由服务层以「启动器」的形式交进来，这里不导入 dsh。
"""

from roundtable.core.supercomputer.audit import AuditLog
from roundtable.core.supercomputer.butler import Butler
from roundtable.core.supercomputer.engine import Launch, Launcher, Supercomputer
from roundtable.core.supercomputer.ledger import Ledger
from roundtable.core.supercomputer.scoring import Score, WorkerState, pick, score

__all__ = [
    "AuditLog",
    "Butler",
    "Launch",
    "Launcher",
    "Ledger",
    "Score",
    "Supercomputer",
    "WorkerState",
    "pick",
    "score",
]
