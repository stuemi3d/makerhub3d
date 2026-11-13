"""Template number and abbreviation of attribute values (issue #62).

Existing values get the first letters of the value as a suggestion (with a counter when that
is already taken in the same attribute). Existing templates keep an empty number.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUGGESTION_LENGTH = 2


def suggest(value: str, used: set[str]) -> str:
    letters = "".join(char for char in value if char.isalnum()).casefold()
    base = letters[:SUGGESTION_LENGTH] or "x"
    candidate, count = base, 1
    while candidate in used:
        count += 1
        candidate = f"{base}{count}"
    return candidate


def upgrade() -> None:
    with op.batch_alter_table("article_templates") as batch:
        batch.add_column(sa.Column("number", sa.String(length=50), nullable=True))
        batch.add_column(sa.Column("number_key", sa.String(length=100), nullable=True))
        batch.create_unique_constraint("uq_article_templates_number_key", ["number_key"])
    with op.batch_alter_table("attribute_values") as batch:
        batch.add_column(
            sa.Column("abbreviation", sa.String(length=10), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("abbreviation_key", sa.String(length=20), nullable=False, server_default="")
        )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, attribute_id, value FROM attribute_values ORDER BY attribute_id, id")
    ).all()
    used: dict[int, set[str]] = {}
    for row in rows:
        taken = used.setdefault(row.attribute_id, set())
        abbreviation = suggest(row.value, taken)
        taken.add(abbreviation)
        connection.execute(
            sa.text(
                "UPDATE attribute_values SET abbreviation = :a, abbreviation_key = :a"
                " WHERE id = :id"
            ),
            {"a": abbreviation, "id": row.id},
        )
    with op.batch_alter_table("attribute_values") as batch:
        batch.create_unique_constraint(
            "uq_attribute_values_abbreviation", ["attribute_id", "abbreviation_key"]
        )


def downgrade() -> None:
    with op.batch_alter_table("attribute_values") as batch:
        batch.drop_constraint("uq_attribute_values_abbreviation", type_="unique")
        batch.drop_column("abbreviation_key")
        batch.drop_column("abbreviation")
    with op.batch_alter_table("article_templates") as batch:
        batch.drop_constraint("uq_article_templates_number_key", type_="unique")
        batch.drop_column("number_key")
        batch.drop_column("number")
