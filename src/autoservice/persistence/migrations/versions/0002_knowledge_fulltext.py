"""Полнотекстовый поиск по библиотеке автодела (SQLite FTS5).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-06
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5("
        "title, body, tags, tokenize = 'unicode61 remove_diacritics 2')"
    )
    # Индексируем записи, которые могли появиться до этой версии.
    op.execute(
        "INSERT INTO knowledge_fts(rowid, title, body, tags) "
        "SELECT id, title, content_text, trim(make || ' ' || model || ' ' || engine) FROM knowledge_sources"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_fts")
