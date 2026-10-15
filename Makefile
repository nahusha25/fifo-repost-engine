.PHONY: test lint typecheck demo

test:
	pytest -q

lint:
	ruff check .

typecheck:
	mypy src

demo:
	@python -c "from decimal import Decimal; from stock_repost import FifoQueue; q = FifoQueue(); q.add(Decimal('10'), Decimal('100')); q.add(Decimal('5'), Decimal('200')); r = q.consume(Decimal('12')); print('Consumed 12 units:', r); print('Remaining: qty=%s value=%s' % (q.balance_qty, q.balance_value))"
