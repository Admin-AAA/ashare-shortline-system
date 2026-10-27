"""文本事件原文和展示用标签。

不改 news_announcement，也不改任何选股表。启动时 alembic upgrade head 会建这两张表。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import MEDIUMTEXT

_LONG = MEDIUMTEXT().with_variant(sa.Text(), "sqlite")


revision = "0029_text_event_labels"
down_revision = "0028_row_updated_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("text_event"):
        op.create_table(
            "text_event",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("source", sa.String(length=40), nullable=False),
            sa.Column("event_id", sa.String(length=80), nullable=False),
            sa.Column("code", sa.String(length=6), nullable=False, server_default=""),
            sa.Column("title", sa.Text(), nullable=True),
            sa.Column("body", _LONG, nullable=True),
            sa.Column("publish_date", sa.String(length=10), nullable=False),
            sa.Column("publish_time", sa.String(length=19), nullable=True),
            sa.Column("available_at", sa.String(length=19), nullable=True),
            sa.Column("time_quality", sa.String(length=16), nullable=False, server_default="date_only"),
            sa.Column("signal_trade_date", sa.String(length=10), nullable=True),
            sa.Column("entry_trade_date", sa.String(length=10), nullable=True),
            sa.Column("provider", sa.String(length=40), nullable=True),
            sa.Column("payload_json", _LONG, nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source", "event_id", name="uq_text_event_source_event"),
        )
    indexes = {str(item.get("name")) for item in inspector.get_indexes("text_event")} if inspector.has_table("text_event") else set()
    # get_indexes before create sees the old inspector cache on some dialects; re-read.
    indexes = {str(item.get("name")) for item in sa.inspect(bind).get_indexes("text_event")}
    if "ix_text_event_code_date" not in indexes:
        op.create_index("ix_text_event_code_date", "text_event", ["code", "publish_date"])
    if "ix_text_event_publish_date" not in indexes:
        op.create_index("ix_text_event_publish_date", "text_event", ["publish_date"])
    if not sa.inspect(bind).has_table("text_event_label"):
        op.create_table(
            "text_event_label",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("source", sa.String(length=40), nullable=False),
            sa.Column("event_id", sa.String(length=80), nullable=False),
            sa.Column("code", sa.String(length=6), nullable=False, server_default=""),
            sa.Column("publish_date", sa.String(length=10), nullable=False),
            sa.Column("available_at", sa.String(length=19), nullable=True),
            sa.Column("label", sa.String(length=8), nullable=True),
            sa.Column("category", sa.String(length=16), nullable=True),
            sa.Column("score", sa.Integer(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("rationale", _LONG, nullable=True),
            sa.Column("model", sa.String(length=80), nullable=True),
            sa.Column("prompt_version", sa.String(length=32), nullable=False),
            sa.Column("label_origin", sa.String(length=16), nullable=True),
            sa.Column("keyword_label", sa.String(length=8), nullable=True),
            sa.Column("keyword_sentiment", sa.String(length=20), nullable=True),
            sa.Column("keyword_hits", sa.String(length=200), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("input_hash", sa.String(length=64), nullable=False),
            sa.Column("prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("completion_tokens", sa.Integer(), nullable=True),
            sa.Column("latency_ms", sa.Float(), nullable=True),
            sa.Column("labeled_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source", "event_id", "prompt_version", name="uq_text_event_label_version"),
        )
    label_indexes = {str(item.get("name")) for item in sa.inspect(bind).get_indexes("text_event_label")}
    if "ix_text_event_label_code_date" not in label_indexes:
        op.create_index("ix_text_event_label_code_date", "text_event_label", ["code", "publish_date"])
    if "ix_text_event_label_hash" not in label_indexes:
        op.create_index("ix_text_event_label_hash", "text_event_label", ["input_hash"])


def downgrade() -> None:
    raise RuntimeError("forward-only migration: text_event tables must not be dropped")
