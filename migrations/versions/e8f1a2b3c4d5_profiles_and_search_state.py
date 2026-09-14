"""Add profile-owned search state and feedback.

Products, listings, market snapshots and opportunities intentionally remain
global.  This revision only gives user-authored state a profile owner.
"""

from typing import Sequence, Union
import datetime as dt

from alembic import op
import sqlalchemy as sa


revision: str = "e8f1a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = "d7e9f3a8b4c1"
branch_labels = None
depends_on = None


MIGRATED_PROFILE_ID = "profile-migrated"


def _add_profile_foreign_key(table: str, column: str) -> None:
    """SQLite cannot add constraints after table creation.

    The ORM still declares the relationship and all API writes validate the
    owner.  PostgreSQL receives the explicit database constraint.
    """

    if op.get_context().dialect.name != "sqlite":
        op.create_foreign_key(
            f"fk_{table}_{column}", table, "profiles", [column], ["id"]
        )


def _drop_profile_foreign_key(table: str, column: str) -> None:
    if op.get_context().dialect.name != "sqlite":
        op.drop_constraint(f"fk_{table}_{column}", table, type_="foreignkey")


def _as_datetime(value):
    """SQLite text selects do not always apply a DateTime result processor."""
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min)
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("name_normalized", sa.String(), nullable=False),
        sa.Column("preferences", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name_normalized", name="uq_profiles_name_normalized"),
    )
    op.create_index("ix_profiles_name_normalized", "profiles", ["name_normalized"])

    # Every previous single-user installation gets one stable owner.  The
    # conditional insert makes rerunning an interrupted migration harmless.
    op.execute(
        sa.text(
            """
            INSERT INTO profiles (id, name, name_normalized, preferences, created_at, updated_at)
            SELECT :id, :name, :normalized, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            WHERE NOT EXISTS (SELECT 1 FROM profiles WHERE id = :id)
            """
        ).bindparams(
            id=MIGRATED_PROFILE_ID,
            name="Perfil migrado",
            normalized="perfil migrado",
        )
    )

    op.add_column("user_preferences", sa.Column("profile_id", sa.String(), nullable=True))
    op.create_index("ix_user_preferences_profile_id", "user_preferences", ["profile_id"])
    _add_profile_foreign_key("user_preferences", "profile_id")

    op.add_column("search_scopes", sa.Column("profile_id", sa.String(), nullable=True))
    op.create_index("ix_search_scopes_profile_id", "search_scopes", ["profile_id"])
    _add_profile_foreign_key("search_scopes", "profile_id")

    op.add_column("pipeline_runs", sa.Column("profile_id", sa.String(), nullable=True))
    op.create_index("ix_pipeline_runs_profile_id", "pipeline_runs", ["profile_id"])
    _add_profile_foreign_key("pipeline_runs", "profile_id")

    for table in ("user_preferences", "search_scopes", "pipeline_runs"):
        op.execute(
            sa.text(f"UPDATE {table} SET profile_id = :id WHERE profile_id IS NULL").bindparams(
                id=MIGRATED_PROFILE_ID
            )
        )

    # The nullable add is required for existing rows.  Once all have the
    # migrated owner, make the schema enforce the same ownership invariant as
    # new rows. batch_alter_table keeps this testable on SQLite too.
    for table in ("user_preferences", "search_scopes", "pipeline_runs"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("profile_id", existing_type=sa.String(), nullable=False)

    # Preserve prior settings in the profile's canonical preference document.
    op.execute(
        sa.text(
            """
            UPDATE profiles
            SET preferences = (
                SELECT preferences FROM user_preferences
                WHERE user_preferences.profile_id = profiles.id
                ORDER BY user_preferences.id ASC LIMIT 1
            )
            WHERE id = :id
              AND EXISTS (
                SELECT 1 FROM user_preferences
                WHERE user_preferences.profile_id = profiles.id
            )
            """
        ).bindparams(id=MIGRATED_PROFILE_ID)
    )

    op.create_table(
        "search_definitions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("profile_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("intent", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("intent_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("plan", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_definitions_profile_id", "search_definitions", ["profile_id"])

    op.create_table(
        "opportunity_feedback",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("profile_id", sa.String(), nullable=False),
        sa.Column("listing_id", sa.String(), nullable=False),
        sa.Column("feedback", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"]),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "listing_id", name="uq_opportunity_feedback_profile_listing"),
    )
    op.create_index("ix_opportunity_feedback_profile_id", "opportunity_feedback", ["profile_id"])
    op.create_index("ix_opportunity_feedback_listing_id", "opportunity_feedback", ["listing_id"])

    op.create_table(
        "search_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("profile_id", sa.String(), nullable=False),
        sa.Column("search_definition_id", sa.String(), nullable=True),
        sa.Column("pipeline_run_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("draft", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("clarification", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("confirmed_plan", sa.JSON(), nullable=True),
        sa.Column("execution", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"]),
        sa.ForeignKeyConstraint(["search_definition_id"], ["search_definitions.id"]),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("profile_id", "search_definition_id", "pipeline_run_id", "status"):
        op.create_index(f"ix_search_sessions_{column}", "search_sessions", [column])

    # Preserve the old SearchScope UI/API as a compatibility surface while
    # materialising its exact intent and executable plan for the new flow.
    # Stable IDs make this data backfill idempotent if a non-transactional
    # backend resumes after table creation.
    bind = op.get_bind()
    legacy_scopes = bind.execute(
        sa.text(
            """
            SELECT id, profile_id, name, marketplace, product_id, query,
                   category, min_price, max_price, sort, "limit", enabled,
                   created_at, updated_at
            FROM search_scopes
            """
        )
    ).mappings().all()
    existing_definition_ids = set(
        bind.execute(sa.text("SELECT id FROM search_definitions")).scalars().all()
    )
    definition_table = sa.table(
        "search_definitions",
        sa.column("id", sa.String()),
        sa.column("profile_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("intent", sa.JSON()),
        sa.column("intent_version", sa.Integer()),
        sa.column("plan", sa.JSON()),
        sa.column("plan_version", sa.Integer()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    materialized_definitions = []
    for scope in legacy_scopes:
        definition_id = f"search-def-legacy-{scope['id']}"
        if definition_id in existing_definition_ids:
            continue
        scope_snapshot = {
            "id": scope["id"],
            "name": scope["name"],
            "marketplace": scope["marketplace"],
            "product_id": scope["product_id"],
            "query": scope["query"],
            "category": scope["category"],
            "min_price": scope["min_price"],
            "max_price": scope["max_price"],
            "sort": scope["sort"],
            "limit": scope["limit"],
            "enabled": scope["enabled"],
        }
        materialized_definitions.append({
            "id": definition_id,
            "profile_id": scope["profile_id"],
            "name": scope["name"],
            "intent": {
                "query": scope["query"],
                "product_id": scope["product_id"],
                "category": scope["category"],
                "min_price": scope["min_price"],
                "max_price": scope["max_price"],
            },
            "intent_version": 1,
            "plan": {
                "source_type": scope["marketplace"],
                "query": scope["query"],
                "limit": scope["limit"],
                "scope_ids": [scope["id"]],
                "scopes": [scope_snapshot],
            },
            "plan_version": 1,
            "created_at": _as_datetime(scope["created_at"]),
            "updated_at": _as_datetime(scope["updated_at"] or scope["created_at"]),
        })
    if materialized_definitions:
        op.bulk_insert(definition_table, materialized_definitions)

    op.create_table(
        "search_session_messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("search_session_id", sa.String(), nullable=False),
        sa.Column("client_request_id", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["search_session_id"], ["search_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "search_session_id",
            "client_request_id",
            name="uq_search_session_messages_session_request",
        ),
    )
    op.create_index("ix_search_session_messages_search_session_id", "search_session_messages", ["search_session_id"])
    op.create_index("ix_search_session_messages_created_at", "search_session_messages", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_search_session_messages_created_at", table_name="search_session_messages")
    op.drop_index("ix_search_session_messages_search_session_id", table_name="search_session_messages")
    op.drop_table("search_session_messages")
    for column in ("status", "pipeline_run_id", "search_definition_id", "profile_id"):
        op.drop_index(f"ix_search_sessions_{column}", table_name="search_sessions")
    op.drop_table("search_sessions")
    op.drop_index("ix_opportunity_feedback_listing_id", table_name="opportunity_feedback")
    op.drop_index("ix_opportunity_feedback_profile_id", table_name="opportunity_feedback")
    op.drop_table("opportunity_feedback")
    op.drop_index("ix_search_definitions_profile_id", table_name="search_definitions")
    op.drop_table("search_definitions")
    _drop_profile_foreign_key("pipeline_runs", "profile_id")
    op.drop_index("ix_pipeline_runs_profile_id", table_name="pipeline_runs")
    op.drop_column("pipeline_runs", "profile_id")
    _drop_profile_foreign_key("search_scopes", "profile_id")
    op.drop_index("ix_search_scopes_profile_id", table_name="search_scopes")
    op.drop_column("search_scopes", "profile_id")
    _drop_profile_foreign_key("user_preferences", "profile_id")
    op.drop_index("ix_user_preferences_profile_id", table_name="user_preferences")
    op.drop_column("user_preferences", "profile_id")
    op.drop_index("ix_profiles_name_normalized", table_name="profiles")
    op.drop_table("profiles")
