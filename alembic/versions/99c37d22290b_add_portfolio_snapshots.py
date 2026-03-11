"""add_portfolio_snapshots

Revision ID: 99c37d22290b
Revises: 48839d97e372
Create Date: 2026-03-10 23:03:08.515541

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '99c37d22290b'
down_revision: Union[str, None] = '48839d97e372'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'portfolio_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('snapshot_date', sa.Date(), nullable=False),
        sa.Column('available_cash', sa.Float(), nullable=False),
        sa.Column('used_margin', sa.Float(), nullable=False),
        sa.Column('total_capital', sa.Float(), nullable=False),
        sa.Column('holdings_value', sa.Float(), nullable=False),
        sa.Column('total_invested', sa.Float(), nullable=False),
        sa.Column('total_pnl', sa.Float(), nullable=False),
        sa.Column('total_pnl_pct', sa.Float(), nullable=False),
        sa.Column('holdings_count', sa.Integer(), nullable=False),
        sa.Column('holdings_json', sa.JSON(), nullable=True),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_portfolio_snapshots_id', 'portfolio_snapshots', ['id'], unique=False)
    op.create_index('ix_portfolio_snapshots_snapshot_date', 'portfolio_snapshots', ['snapshot_date'], unique=False)
    op.create_index('ix_portfolio_snapshots_user_id', 'portfolio_snapshots', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_portfolio_snapshots_user_id', table_name='portfolio_snapshots')
    op.drop_index('ix_portfolio_snapshots_snapshot_date', table_name='portfolio_snapshots')
    op.drop_index('ix_portfolio_snapshots_id', table_name='portfolio_snapshots')
    op.drop_table('portfolio_snapshots')
