"""fix incorrectly scaled negative percentages"""
from alembic import op

revision = "20260915_03"
down_revision = "20260914_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Старый парсер умножал любое отрицательное значение на 100, поскольку
    # проверял `value <= 1`. Исправляем уже загруженные скидки вроде -1700%.
    # Если базовая сумма известна, сравниваем исходное значение и его вариант,
    # уменьшенный в 100 раз, с фактической скидкой по суммам. Это исправляет
    # как -1700 вместо -17, так и -60 вместо -0.6, не затрагивая корректные
    # проценты, полученные из числовой Excel-ячейки.
    op.execute(
        "UPDATE sales SET discount_percent = discount_percent / 100 "
        "WHERE discount_percent < 0 AND base_amount IS NOT NULL AND base_amount <> 0 "
        "AND abs((discount_percent / 100) - ((total_amount / base_amount - 1) * 100)) "
        "< abs(discount_percent - ((total_amount / base_amount - 1) * 100))"
    )
    op.execute(
        "UPDATE sales SET discount_percent = discount_percent / 100 "
        "WHERE discount_percent <= -100 AND (base_amount IS NULL OR base_amount = 0)"
    )
    op.execute(
        "UPDATE sales SET discount_card_percent = discount_card_percent / 100 "
        "WHERE discount_card_percent <= -100"
    )


def downgrade() -> None:
    # Автоматически отличить ранее ошибочное значение от корректного после
    # исправления невозможно, поэтому data migration намеренно необратима.
    pass
