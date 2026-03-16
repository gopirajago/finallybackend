"""Add strategy tables

Revision ID: b2625ed7fba6
Revises: 1fb7cbf34697
Create Date: 2026-03-16 07:18:56.086412

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2625ed7fba6'
down_revision: Union[str, None] = '1fb7cbf34697'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create strategy_signals table
    op.create_table(
        'strategy_signals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('strategy_name', sa.String(), nullable=False),
        sa.Column('symbol', sa.String(), nullable=False),
        sa.Column('signal_type', sa.String(), nullable=False),
        sa.Column('alpha1', sa.Float(), nullable=False),
        sa.Column('alpha2', sa.Float(), nullable=False),
        sa.Column('strike_price', sa.Float(), nullable=False),
        sa.Column('option_type', sa.String(), nullable=False),
        sa.Column('expiry_date', sa.String(), nullable=False),
        sa.Column('atm_strike', sa.Float(), nullable=False),
        sa.Column('spot_price', sa.Float(), nullable=False),
        sa.Column('option_price', sa.Float(), nullable=False),
        sa.Column('otm_call_volume_ratio', sa.Float(), nullable=True),
        sa.Column('itm_put_volume_ratio', sa.Float(), nullable=True),
        sa.Column('otm_call_oi_change', sa.Float(), nullable=True),
        sa.Column('itm_put_oi_change', sa.Float(), nullable=True),
        sa.Column('otm_call_iv', sa.Float(), nullable=True),
        sa.Column('itm_call_iv', sa.Float(), nullable=True),
        sa.Column('otm_put_iv', sa.Float(), nullable=True),
        sa.Column('itm_put_iv', sa.Float(), nullable=True),
        sa.Column('signal_strength', sa.Float(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('is_traded', sa.Boolean(), nullable=True),
        sa.Column('signal_time', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_strategy_signals_id'), 'strategy_signals', ['id'], unique=False)
    
    # Create strategy_trades table
    op.create_table(
        'strategy_trades',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('signal_id', sa.Integer(), nullable=True),
        sa.Column('strategy_name', sa.String(), nullable=False),
        sa.Column('strategy_version', sa.String(), nullable=True),
        sa.Column('symbol', sa.String(), nullable=False),
        sa.Column('trade_type', sa.String(), nullable=False),
        sa.Column('strike_price', sa.Float(), nullable=False),
        sa.Column('option_type', sa.String(), nullable=False),
        sa.Column('expiry_date', sa.String(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('entry_price', sa.Float(), nullable=False),
        sa.Column('entry_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('entry_alpha1', sa.Float(), nullable=True),
        sa.Column('entry_alpha2', sa.Float(), nullable=True),
        sa.Column('exit_price', sa.Float(), nullable=True),
        sa.Column('exit_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('exit_reason', sa.String(), nullable=True),
        sa.Column('stop_loss_price', sa.Float(), nullable=False),
        sa.Column('trailing_stop_price', sa.Float(), nullable=True),
        sa.Column('highest_price', sa.Float(), nullable=True),
        sa.Column('pnl', sa.Float(), nullable=True),
        sa.Column('pnl_percent', sa.Float(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['signal_id'], ['strategy_signals.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_strategy_trades_id'), 'strategy_trades', ['id'], unique=False)
    
    # Create strategy_configs table
    op.create_table(
        'strategy_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('strategy_name', sa.String(), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=True),
        sa.Column('version', sa.String(), nullable=True),
        sa.Column('symbols', sa.JSON(), nullable=True),
        sa.Column('start_time', sa.String(), nullable=True),
        sa.Column('end_time', sa.String(), nullable=True),
        sa.Column('alpha1_long_call_threshold', sa.Float(), nullable=True),
        sa.Column('alpha2_long_call_threshold', sa.Float(), nullable=True),
        sa.Column('alpha1_long_put_threshold', sa.Float(), nullable=True),
        sa.Column('alpha2_long_put_threshold', sa.Float(), nullable=True),
        sa.Column('min_option_price', sa.Float(), nullable=True),
        sa.Column('stop_loss_percent', sa.Float(), nullable=True),
        sa.Column('trailing_stop_percent', sa.Float(), nullable=True),
        sa.Column('default_quantity', sa.Integer(), nullable=True),
        sa.Column('max_positions', sa.Integer(), nullable=True),
        sa.Column('send_signal_alerts', sa.Boolean(), nullable=True),
        sa.Column('send_trade_alerts', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index(op.f('ix_strategy_configs_id'), 'strategy_configs', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_strategy_configs_id'), table_name='strategy_configs')
    op.drop_table('strategy_configs')
    op.drop_index(op.f('ix_strategy_trades_id'), table_name='strategy_trades')
    op.drop_table('strategy_trades')
    op.drop_index(op.f('ix_strategy_signals_id'), table_name='strategy_signals')
    op.drop_table('strategy_signals')
