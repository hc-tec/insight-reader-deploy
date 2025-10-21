#!/usr/bin/env python3
"""
Database initialization script

Usage:
    python init_database.py

This script creates all database tables defined in the models.
Run this after setting up your PostgreSQL database for the first time.
"""

import sys
from app.db.database import init_db
from app.config import settings


def main():
    """Initialize database tables"""
    print("=" * 60)
    print("Database Initialization Script")
    print("=" * 60)

    print(f"\nDatabase URL: {settings.database_url}")

    if "postgresql" in settings.database_url:
        print("Database Type: PostgreSQL")
    else:
        print("Database Type: SQLite")

    print("\nInitializing database tables...")

    try:
        init_db()
        print("\n[SUCCESS] Database tables created successfully!")
        print("\nCreated tables:")
        print("  - users")
        print("  - articles")
        print("  - insights")
        print("  - collections")
        print("  - sparks")
        print("  - meta_analysis")
        print("  - thinking_lens")
        print("  - insight_history")
        print("  - preferences")
        print("\n" + "=" * 60)
        return 0

    except Exception as e:
        print(f"\n[ERROR] Failed to initialize database")
        print(f"Error details: {str(e)}")
        print("\nPlease check:")
        print("  1. Database connection string is correct")
        print("  2. Database server is running")
        print("  3. Database user has proper permissions")
        print("  4. Database exists")
        print("\n" + "=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
