import sys
from packages.core.database import Base, engine
from packages.core.seed import generate_seed

def reset_db():
    print("Dropping all tables...")
    Base.metadata.drop_all(bind=engine)
    print("Recreating all tables...")
    Base.metadata.create_all(bind=engine)
    print("Database reset completed successfully.")

def seed_fixtures():
    print("Seeding synthetic fixtures...")
    res = generate_seed()
    print(f"Seeding completed: {res['listings']} listings, {res['products']} products, {res['opportunities']} opportunities.")

def reconcile_pipelines(dry_run: bool = False):
    from packages.pipeline.high_volume import reconcile_historical_runs

    result = reconcile_historical_runs(dry_run=dry_run)
    mode = "dry-run" if dry_run else "applied"
    print(f"Pipeline reconciliation {mode}: {result}")

def main():
    if len(sys.argv) < 2:
        print("Usage: python -m apps.worker.cli [reset-db | seed-fixtures | reconcile-pipelines [--dry-run]]")
        sys.exit(1)
    
    cmd = sys.argv[1]
    if cmd == "reset-db":
        reset_db()
    elif cmd == "seed-fixtures":
        seed_fixtures()
    elif cmd == "reconcile-pipelines":
        reconcile_pipelines("--dry-run" in sys.argv[2:])
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

if __name__ == "__main__":
    main()
