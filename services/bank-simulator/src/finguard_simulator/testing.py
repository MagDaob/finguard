"""仅供离线回放和旧回归套件使用的显式同库测试夹具。"""


def seed_combined_demo(database):
    from finguard.backend.demo import seed_demo as seed_app
    from finguard_simulator.database import Database
    from finguard_simulator.demo import seed_demo as seed_bank

    bank_db = Database(database.dsn, database.schema)
    bank_db.migrate()
    seed_app(database)
    seed_bank(bank_db)
