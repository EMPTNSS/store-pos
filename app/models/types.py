from sqlalchemy import Numeric

QUANTITY_PRECISION = 10
QUANTITY_SCALE = 3


def quantity_column() -> Numeric:
    return Numeric(QUANTITY_PRECISION, QUANTITY_SCALE)
