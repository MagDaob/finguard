"""只建立项目侧演示主体；银行数据由模拟服务独立管理。"""

from finguard.backend.database import Database
from finguard.backend.security import Security


def seed_demo(database: Database) -> None:
    security = Security(database)
    for subject, name, risk in (
        ("user-demo-001", "演示用户", 2),
        ("user-demo-002", "演示收款人", 1),
        ("operator-demo", "演示维护员", 1),
    ):
        security.provision(subject, name, risk)
