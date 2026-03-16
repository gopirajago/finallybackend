"""add multi-strategy support

Revision ID: c3d4e5f6g7h8
Revises: b2625ed7fba6
Create Date: 2026-03-16 08:11:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c3d4e5f6g7h8'
down_revision = 'b2625ed7fba6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add strategy_type column
    op.add_column('strategy_configs', sa.Column('strategy_type', sa.String(), nullable=True))
    
    # Add enabled_strategies column (JSON array)
    op.add_column('strategy_configs', sa.Column('enabled_strategies', sa.JSON(), nullable=True))
    
    # Add strategy_allocation column (JSON object)
    op.add_column('strategy_configs', sa.Column('strategy_allocation', sa.JSON(), nullable=True))
    
    # Set default values for existing rows
    op.execute("UPDATE strategy_configs SET strategy_type = 'skew_hunter' WHERE strategy_type IS NULL")
    op.execute("UPDATE strategy_configs SET enabled_strategies = '[\"skew_hunter\"]' WHERE enabled_strategies IS NULL")
    op.execute("UPDATE strategy_configs SET strategy_allocation = '{\"skew_hunter\": 100}' WHERE strategy_allocation IS NULL")
    
    # Make columns non-nullable after setting defaults
    op.alter_column('strategy_configs', 'strategy_type', nullable=False)


def downgrade() -> None:
    op.drop_column('strategy_configs', 'strategy_allocation')
    op.drop_column('strategy_configs', 'enabled_strategies')
    op.drop_column('strategy_configs', 'strategy_type')
