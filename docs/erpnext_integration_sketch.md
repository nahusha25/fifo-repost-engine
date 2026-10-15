# ERPNext Integration Sketch

> **Design sketch, not tested against ERPNext.** Names marked with `(verify against ERPNext source)` should be checked against the target ERPNext version.

```python
# 1. Edit/Cancel of submitted Purchase Receipt creates Repost Item Valuation
def on_cancel_or_update(doc, method=None):
    repost_doc = frappe.get_doc({
        "doctype": "Repost Item Valuation",  # (verify against ERPNext source)
        "item_code": doc.item_code,
        "warehouse": doc.warehouse,
        "posting_date": doc.posting_date,
        "posting_time": doc.posting_time,
        "status": "Queued",  # (verify against ERPNext source)
    }).insert(ignore_permissions=True)
    frappe.enqueue("erpnext.stock.repost_item_valuation.repost",  # (verify against ERPNext source)
                   docname=repost_doc.name)

# 2. Background Repost Item Valuation worker processes dirty keys
def execute_repost(repost_doc):
    repost_doc.status = "In Progress"  # (verify against ERPNext source)
    work_queue = PriorityQueue()
    work_queue.push((repost_doc.posting_date, repost_doc.item_code, repost_doc.warehouse))

    while not work_queue.empty():
        posting_datetime, item, warehouse = work_queue.pop()

        # 3. Per item/warehouse replay (update_entries_after logic) (verify against ERPNext source)
        prev_sle = get_previous_sle(item, warehouse, posting_datetime)
        fifo_queue = restore_fifo_queue(prev_sle.stock_queue)  # (verify against ERPNext source)

        for sle in get_future_sles(item, warehouse, posting_datetime):
            old_rate, old_bal_val = sle.valuation_rate, sle.stock_value

            if sle.actual_qty > 0:
                fifo_queue.push(sle.actual_qty, sle.incoming_rate)
            else:
                consumed_value = fifo_queue.pop(abs(sle.actual_qty))
                sle.valuation_rate = consumed_value / abs(sle.actual_qty)

                # 4. Cascade to dependent finished-good SLEs
                if sle.voucher_type == "Stock Entry" and sle.dependant_sle_voucher_detail_no:  # (verify against ERPNext source)
                    fg_sle = get_sle_by_detail(sle.dependant_sle_voucher_detail_no)  # (verify against ERPNext source)
                    fg_sle.incoming_rate = consumed_value / fg_sle.actual_qty
                    save_sle(fg_sle)
                    work_queue.push((fg_sle.posting_datetime, fg_sle.item_code, fg_sle.warehouse))

            # Early stop when rate and queue converge strictly after max dirty time
            if can_early_stop(sle, old_rate, old_bal_val, fifo_queue):
                break
            save_sle(sle)

    # 5. General Ledger reposting
    repost_future_sle_and_gle(repost_doc)  # (verify against ERPNext source)
    repost_doc.status = "Completed"  # (verify against ERPNext source)
```
