#!/usr/bin/env python3
from packages.core.seed import generate_seed

if __name__ == "__main__":
    res = generate_seed()
    print(f"Database seeded successfully with {res['listings']} listings, {res['products']} products, {res['opportunities']} opportunities.")
