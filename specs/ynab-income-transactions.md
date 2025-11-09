# YNAB Income Transactions - Recognize "Inflow: Ready to Assign"

**Status:** Draft
**Created:** 2025-11-09
**Author:** System

## Overview

Automatically recognize and categorize YNAB transactions with the category "Inflow: Ready to Assign" as income transactions in moneyflow reports, enabling better income vs. expense analysis and reporting.

## Background

### YNAB Income Model

YNAB uses a special category called **"Inflow: Ready to Assign"** (previously "To be Budgeted" in YNAB 4) to represent income that hasn't been assigned to specific budget categories yet:

- **Income transactions**: Paychecks, freelance payments, refunds, reimbursements
- **Category**: "Inflow: Ready to Assign"
- **Amount**: Positive (inflow)
- **Purpose**: Money available to budget but not yet assigned to categories

### Current moneyflow Behavior

Currently, moneyflow treats all YNAB transactions uniformly:

- Category groups have a `type` field (`"income"`, `"expense"`, `"transfer"`)
- YNAB backend sets all category groups to `type: "expense"` (see `ynab_client.py:140`)
- No distinction between income and expense transactions in reports
- "Inflow: Ready to Assign" transactions appear as regular transactions

### Data Model

From `demo_data_generator.py`, moneyflow supports:

```python
# Category group types
{
    "id": "grp_income",
    "name": "Income",
    "type": "income"  # vs "expense" or "transfer"
}
```

## Problem Statement

Users cannot easily:

1. **Distinguish income from expenses** in moneyflow reports
2. **Calculate net income** (total income - total expenses)
3. **Track income trends** over time
4. **Filter or group by income** vs expense transactions
5. **Understand cash flow** (money in vs money out)

This is especially problematic for:

- Freelancers tracking income variability
- Users monitoring salary changes
- Tax preparation and reporting
- Budget planning and forecasting

## Proposed Solution

Automatically detect transactions with category name "Inflow: Ready to Assign" and mark their category group as `type: "income"`.

### Solution Approach

**Option 1: Detect at Transaction Conversion** (RECOMMENDED)

Modify `_convert_transaction()` to check if the category name matches "Inflow: Ready to Assign" and set the category group type accordingly.

**Pros:**
- Simple, localized change
- Works with existing data structures
- No API changes needed
- Preserves category information from YNAB

**Cons:**
- Hardcoded category name (could change in YNAB)
- Per-transaction checking (minimal overhead)

**Option 2: Detect at Category Fetch**

Modify `get_transaction_categories()` to identify "Inflow: Ready to Assign" category and set its group type to "income".

**Pros:**
- One-time check at category load
- Centralized logic

**Cons:**
- YNAB API may not expose this category in category list
- May not appear in category groups API response

**Recommendation:** Use Option 1 (transaction conversion) as it's more reliable and doesn't depend on YNAB's API structure.

## Implementation Plan

### 1. Add Income Category Detection

**Location:** `moneyflow/ynab_client.py:_convert_transaction()`

Add logic to detect "Inflow: Ready to Assign" category and set group type:

```python
def _convert_transaction(self, txn: Any) -> Dict[str, Any]:
    """
    Convert a YNAB transaction to moneyflow-compatible format.

    Special handling for income:
    - Transactions with category "Inflow: Ready to Assign"
      are marked as income type
    """
    # Check if transaction belongs to a tracking account
    is_tracking_account = False
    if self._account_cache and txn.account_id in self._account_cache:
        is_tracking_account = not self._account_cache[txn.account_id]["on_budget"]

    # Determine category group type
    category_name = txn.category_name or "Uncategorized"
    category_group_type = "expense"  # Default

    # Detect income transactions
    if category_name == "Inflow: Ready to Assign":
        category_group_type = "income"

    return {
        "id": txn.id,
        "date": str(txn.var_date),
        "amount": float(txn.amount) / 1000.0,
        "merchant": {
            "id": txn.payee_id or "unknown",
            "name": txn.payee_name or "Unknown",
        },
        "category": {
            "id": txn.category_id or "uncategorized",
            "name": category_name,
            "group": {
                "id": "grp_income" if category_group_type == "income" else "grp_expense",
                "name": "Income" if category_group_type == "income" else "Expense",
                "type": category_group_type,
            },
        },
        "account": {
            "id": txn.account_id,
            "displayName": txn.account_name,
        },
        "notes": txn.memo or "",
        "hideFromReports": (
            txn.deleted
            or txn.transfer_account_id is not None
            or is_tracking_account
        ),
        "pending": txn.cleared == "uncleared",
        "isRecurring": False,
    }
```

### 2. Handle Split Transactions (Future)

YNAB allows splitting income between "Inflow: Ready to Assign" and budget categories (e.g., freelancers setting aside taxes).

**Current scope:** Handle simple case only (full transaction to "Inflow: Ready to Assign")

**Future enhancement:** Support subtransactions with mixed income/expense splits

### 3. Update Category Group Fetching (Optional)

Currently `get_transaction_category_groups()` hardcodes all groups as `type: "expense"`:

```python
# Current (line 159-166)
category_groups = [
    {
        "id": group.id,
        "name": group.name,
        "type": "expense",  # Hardcoded
    }
    for group in response.data.category_groups
]
```

**Consider:** Keep this as-is since category group type is now set per-transaction based on actual usage.

## Testing Strategy

### Unit Tests

**Location:** `tests/test_ynab_backend.py`

#### Test 1: Income Transaction Recognition

```python
@pytest.mark.asyncio
async def test_inflow_ready_to_assign_marked_as_income(backend, mock_ynab_api):
    """Test that 'Inflow: Ready to Assign' transactions are marked as income."""
    backend.client.budget_id = "test-budget-id"
    backend.client.access_token = "test-token"
    backend.client.api_client = MagicMock()

    # Mock income transaction
    mock_txn = MagicMock()
    mock_txn.id = "txn-paycheck"
    mock_txn.var_date = "2025-01-15"
    mock_txn.amount = 500000  # $5000 paycheck
    mock_txn.payee_id = "payee-employer"
    mock_txn.payee_name = "Acme Corp"
    mock_txn.category_id = "cat-income"
    mock_txn.category_name = "Inflow: Ready to Assign"
    mock_txn.account_id = "acc-checking"
    mock_txn.account_name = "Checking"
    mock_txn.memo = "Paycheck"
    mock_txn.deleted = False
    mock_txn.transfer_account_id = None
    mock_txn.cleared = "cleared"

    mock_response = MagicMock()
    mock_response.data.transactions = [mock_txn]

    mock_transactions_api = MagicMock()
    mock_transactions_api.get_transactions.return_value = mock_response

    mock_ynab_api.TransactionsApi.return_value = mock_transactions_api

    result = await backend.get_transactions(limit=10)

    # Verify transaction is marked as income
    txn = result["allTransactions"]["results"][0]
    assert txn["category"]["name"] == "Inflow: Ready to Assign"
    assert txn["category"]["group"]["type"] == "income"
    assert txn["category"]["group"]["name"] == "Income"
    assert txn["amount"] == 5000.0  # Positive amount
```

#### Test 2: Regular Expense Transaction

```python
def test_regular_expense_marked_as_expense(backend):
    """Test that regular expense transactions are marked as expense type."""
    backend.client._account_cache = None

    mock_txn = MagicMock()
    mock_txn.id = "txn-grocery"
    mock_txn.var_date = "2025-01-15"
    mock_txn.amount = -50000  # -$50
    mock_txn.payee_id = "payee-1"
    mock_txn.payee_name = "Grocery Store"
    mock_txn.category_id = "cat-groceries"
    mock_txn.category_name = "Groceries"
    mock_txn.account_id = "acc-checking"
    mock_txn.account_name = "Checking"
    mock_txn.memo = "Weekly shopping"
    mock_txn.deleted = False
    mock_txn.transfer_account_id = None
    mock_txn.cleared = "cleared"

    converted = backend.client._convert_transaction(mock_txn)

    # Verify transaction is marked as expense
    assert converted["category"]["name"] == "Groceries"
    assert converted["category"]["group"]["type"] == "expense"
    assert converted["amount"] == -50.0  # Negative amount
```

#### Test 3: Null Category Handling

```python
def test_null_category_marked_as_expense(backend):
    """Test that transactions with null category are marked as expense."""
    backend.client._account_cache = None

    mock_txn = MagicMock()
    mock_txn.id = "txn-uncategorized"
    mock_txn.var_date = "2025-01-15"
    mock_txn.amount = -25000
    mock_txn.payee_id = "payee-1"
    mock_txn.payee_name = "Unknown Store"
    mock_txn.category_id = None
    mock_txn.category_name = None
    mock_txn.account_id = "acc-checking"
    mock_txn.account_name = "Checking"
    mock_txn.memo = ""
    mock_txn.deleted = False
    mock_txn.transfer_account_id = None
    mock_txn.cleared = "cleared"

    converted = backend.client._convert_transaction(mock_txn)

    # Null category should default to expense
    assert converted["category"]["name"] == "Uncategorized"
    assert converted["category"]["group"]["type"] == "expense"
```

#### Test 4: Case Sensitivity

```python
def test_inflow_category_case_sensitive(backend):
    """Test that category name matching is case-sensitive."""
    backend.client._account_cache = None

    # Test lowercase variant
    mock_txn = MagicMock()
    mock_txn.id = "txn-1"
    mock_txn.var_date = "2025-01-15"
    mock_txn.amount = 100000
    mock_txn.payee_id = "payee-1"
    mock_txn.payee_name = "Test"
    mock_txn.category_id = "cat-1"
    mock_txn.category_name = "inflow: ready to assign"  # Lowercase
    mock_txn.account_id = "acc-1"
    mock_txn.account_name = "Checking"
    mock_txn.memo = ""
    mock_txn.deleted = False
    mock_txn.transfer_account_id = None
    mock_txn.cleared = "cleared"

    converted = backend.client._convert_transaction(mock_txn)

    # Should NOT match (case-sensitive)
    assert converted["category"]["group"]["type"] == "expense"
```

#### Test 5: Multiple Income Transactions

```python
@pytest.mark.asyncio
async def test_multiple_income_transactions(backend, mock_ynab_api):
    """Test that multiple income transactions are all marked correctly."""
    backend.client.budget_id = "test-budget-id"
    backend.client.access_token = "test-token"
    backend.client.api_client = MagicMock()

    # Create multiple income transactions
    income_txns = []
    for i in range(3):
        mock_txn = MagicMock()
        mock_txn.id = f"txn-income-{i}"
        mock_txn.var_date = f"2025-01-{i+1:02d}"
        mock_txn.amount = 500000 + (i * 10000)
        mock_txn.payee_id = f"payee-{i}"
        mock_txn.payee_name = f"Employer {i}"
        mock_txn.category_id = "cat-income"
        mock_txn.category_name = "Inflow: Ready to Assign"
        mock_txn.account_id = "acc-checking"
        mock_txn.account_name = "Checking"
        mock_txn.memo = f"Paycheck {i}"
        mock_txn.deleted = False
        mock_txn.transfer_account_id = None
        mock_txn.cleared = "cleared"
        income_txns.append(mock_txn)

    mock_response = MagicMock()
    mock_response.data.transactions = income_txns

    mock_transactions_api = MagicMock()
    mock_transactions_api.get_transactions.return_value = mock_response

    mock_ynab_api.TransactionsApi.return_value = mock_transactions_api

    result = await backend.get_transactions(limit=10)

    # Verify all are marked as income
    txns = result["allTransactions"]["results"]
    assert len(txns) == 3
    for txn in txns:
        assert txn["category"]["group"]["type"] == "income"
        assert txn["amount"] > 0
```

#### Test 6: Mixed Income and Expense

```python
@pytest.mark.asyncio
async def test_mixed_income_and_expense_transactions(backend, mock_ynab_api):
    """Test that income and expense transactions coexist correctly."""
    backend.client.budget_id = "test-budget-id"
    backend.client.access_token = "test-token"
    backend.client.api_client = MagicMock()

    # Income transaction
    income_txn = MagicMock()
    income_txn.id = "txn-income"
    income_txn.var_date = "2025-01-01"
    income_txn.amount = 500000
    income_txn.payee_id = "payee-employer"
    income_txn.payee_name = "Employer"
    income_txn.category_id = "cat-income"
    income_txn.category_name = "Inflow: Ready to Assign"
    income_txn.account_id = "acc-checking"
    income_txn.account_name = "Checking"
    income_txn.memo = "Paycheck"
    income_txn.deleted = False
    income_txn.transfer_account_id = None
    income_txn.cleared = "cleared"

    # Expense transaction
    expense_txn = MagicMock()
    expense_txn.id = "txn-expense"
    expense_txn.var_date = "2025-01-02"
    expense_txn.amount = -50000
    expense_txn.payee_id = "payee-grocery"
    expense_txn.payee_name = "Grocery Store"
    expense_txn.category_id = "cat-groceries"
    expense_txn.category_name = "Groceries"
    expense_txn.account_id = "acc-checking"
    expense_txn.account_name = "Checking"
    expense_txn.memo = "Shopping"
    expense_txn.deleted = False
    expense_txn.transfer_account_id = None
    expense_txn.cleared = "cleared"

    mock_response = MagicMock()
    mock_response.data.transactions = [income_txn, expense_txn]

    mock_transactions_api = MagicMock()
    mock_transactions_api.get_transactions.return_value = mock_response

    mock_ynab_api.TransactionsApi.return_value = mock_transactions_api

    result = await backend.get_transactions(limit=10)

    # Verify correct categorization
    txns = result["allTransactions"]["results"]
    assert len(txns) == 2

    income = [t for t in txns if t["id"] == "txn-income"][0]
    assert income["category"]["group"]["type"] == "income"
    assert income["amount"] > 0

    expense = [t for t in txns if t["id"] == "txn-expense"][0]
    assert expense["category"]["group"]["type"] == "expense"
    assert expense["amount"] < 0
```

### Integration Tests

#### Test 7: End-to-End Income Recognition

```python
@pytest.mark.integration
def test_ynab_income_recognition_e2e():
    """Test complete workflow with real YNAB API (requires test account)."""
    backend = YNABBackend()
    await backend.login(password=os.getenv("YNAB_TOKEN"))

    transactions = await backend.get_transactions(limit=1000)

    # Find income transactions
    all_txns = transactions["allTransactions"]["results"]
    income_txns = [t for t in all_txns if t["category"]["group"]["type"] == "income"]

    # Verify income transactions exist and have positive amounts
    if income_txns:
        for txn in income_txns:
            assert txn["category"]["name"] == "Inflow: Ready to Assign"
            # Note: Amount could be negative if it's a reversal/correction
            assert txn["category"]["group"]["name"] == "Income"
```

### Manual Testing Checklist

- [ ] Test with real YNAB account containing income transactions
- [ ] Verify income transactions appear with correct category group type
- [ ] Verify expense transactions still work correctly
- [ ] Test filtering/grouping by category group type (if available in UI)
- [ ] Verify income transactions are not hidden by default
- [ ] Test with accounts containing only income (e.g., investment dividends)
- [ ] Test with mixed income/expense in same account
- [ ] Verify stats/totals calculations handle income correctly

## Edge Cases and Considerations

### 1. Category Name Variations

**Scenario:** YNAB changes the category name from "Inflow: Ready to Assign"

**Handling:** Use exact string match for now. Consider future enhancement to detect by category ID or API field.

**Mitigation:**
```python
# Future-proof: Support both old and new names
INCOME_CATEGORY_NAMES = [
    "Inflow: Ready to Assign",
    "To be Budgeted",  # YNAB 4 legacy
    "Income for [Month]",  # Possible future variant
]

if category_name in INCOME_CATEGORY_NAMES:
    category_group_type = "income"
```

### 2. Null Category Name

**Scenario:** Transaction has `category_name = None`

**Handling:** Default to expense type (current behavior)

```python
category_name = txn.category_name or "Uncategorized"
```

### 3. Negative Income Amounts

**Scenario:** Income transaction with negative amount (refund reversal, correction)

**Handling:** Still mark as income based on category, not amount sign. Amount sign indicates direction, not type.

**Example:**
- Paycheck reversal: `category_name = "Inflow: Ready to Assign"`, `amount = -5000`
- Still income type, just negative income

### 4. Transfer Transactions

**Scenario:** Transfer between accounts might have no category

**Handling:** Already marked as hidden (`hideFromReports = True`), so category type less relevant

### 5. Split Transactions

**Scenario:** Income split between "Inflow: Ready to Assign" and budget categories (e.g., freelance income with taxes)

**Handling:** Not supported in Phase 1. YNAB API returns subtransactions separately.

**Future enhancement:** Handle subtransactions array

### 6. Localization

**Scenario:** YNAB in different languages may use translated category names

**Handling:** Research needed. May need to detect by category ID or API metadata instead of name.

**Current scope:** Assume English "Inflow: Ready to Assign"

## Performance Considerations

### Category Name Check

- **When:** For every transaction during conversion
- **Cost:** String comparison (`==` operator)
- **Impact:** Negligible (< 1μs per transaction)

### Memory

- **Additional data:** One extra field per transaction (category group type)
- **Size:** ~20 bytes per transaction
- **Impact:** Negligible for typical datasets (1000 txns = 20KB)

### API Calls

- **No additional API calls required**
- Uses existing transaction data from `get_transactions()`

## Backward Compatibility

### Existing Behavior

Currently, all YNAB transactions have:
```python
"category": {
    "group": {
        "type": "expense"  # Hardcoded in ynab_client.py:140
    }
}
```

### New Behavior

Income transactions will have:
```python
"category": {
    "group": {
        "type": "income"  # Based on category name
    }
}
```

### Impact

- **Breaking change?** No - this is a behavior enhancement, not an API change
- **User-visible impact:** Better categorization in reports (if UI uses group type)
- **Data compatibility:** Existing code expecting "expense" will still work

## Future Enhancements

### 1. UI Support for Income/Expense Filtering

Add UI options to:
- Filter by income vs expense transactions
- Show income totals separately from expense totals
- Calculate net income (income - expenses)
- Group by category type in reports

Example:
```
Summary
├── Income: $5,000
├── Expenses: -$3,500
└── Net: $1,500
```

### 2. Income Categories Beyond "Ready to Assign"

Support users who categorize income into specific categories:
- "Salary"
- "Freelance"
- "Investment Income"
- "Side Hustle"

**Approach:** Allow users to mark categories as "income" type in moneyflow config

### 3. Split Transaction Support

Handle YNAB split transactions where income is divided between:
- "Inflow: Ready to Assign" (net income)
- Budget categories (taxes, retirement contributions)

### 4. Income Trend Analysis

Add reports showing:
- Month-over-month income changes
- Income variability (for freelancers)
- Income sources breakdown
- Year-over-year income comparison

### 5. Tax Reporting

Generate reports for tax purposes:
- Total income by year
- Income by source (payee)
- Categorized income (W-2 vs 1099 vs other)

### 6. Localization Support

Detect income category by:
- Category ID or metadata from API (language-independent)
- Support multiple language variants of "Inflow: Ready to Assign"

## Documentation Updates

### README.md

No changes needed (internal behavior enhancement)

### docs/guide/ynab.md

Add section explaining income recognition:

```markdown
## Income Transactions

moneyflow automatically recognizes YNAB income transactions:

### How It Works

- Transactions categorized as **"Inflow: Ready to Assign"** are marked as income
- Income transactions appear with positive amounts
- Expenses appear with negative amounts

### Income vs Expense

In reports, you can distinguish:
- **Income**: Money coming in (paychecks, freelance, refunds)
- **Expenses**: Money going out (bills, shopping, subscriptions)

This enables better cash flow analysis and budgeting insights.
```

### docs/reference/changelog.md

Add to next release:

```markdown
- **YNAB income recognition** - Automatically recognize "Inflow: Ready to Assign"
  transactions as income for better income vs. expense reporting
```

## Implementation Checklist

- [ ] Update `_convert_transaction()` to detect income category
- [ ] Add `category_group_type` logic based on category name
- [ ] Write 7 unit tests covering various scenarios
- [ ] Write integration test with real YNAB API
- [ ] Manual testing with real YNAB account
- [ ] Run full test suite (`uv run pytest -v`)
- [ ] Run type checker (`uv run pyright moneyflow/`)
- [ ] Update user documentation (docs/guide/ynab.md)
- [ ] Consider future enhancements (split transactions, UI support)

## Review Criteria

Before merging:

- [ ] All tests pass (including new tests)
- [ ] Type checking passes with no errors
- [ ] Code coverage maintained or increased
- [ ] Manual testing confirms income transactions recognized correctly
- [ ] Documentation is clear and complete
- [ ] No breaking changes to existing functionality
- [ ] Edge cases are handled gracefully

## Questions and Open Issues

1. **Should we support category name variations?**
   - **Decision:** Start with exact match "Inflow: Ready to Assign", add variations later if needed

2. **Should we handle split transactions?**
   - **Decision:** No in Phase 1. Add as future enhancement.

3. **How to handle localization?**
   - **Decision:** English only for now. Research category ID detection for future.

4. **Should negative income amounts be allowed?**
   - **Decision:** Yes - mark as income based on category, not amount sign.

5. **Does UI need to display income differently?**
   - **Decision:** Out of scope for backend. UI can use `category.group.type` field.

## Related Issues

- Issue #XX: Add income vs expense filtering in UI
- Issue #XX: Income trend reports
- Issue #XX: Tax reporting features

## References

- [YNAB API Documentation](https://api.youneedabudget.com)
- [YNAB Support - How to Add Income](https://support.ynab.com/en_us/how-to-add-income-and-other-inflows-H1ZNjfZJi)
- [YNAB SDK Python - Transaction Model](https://github.com/ynab/ynab-sdk-python)
- [moneyflow Demo Data Generator](moneyflow/demo_data_generator.py) - Shows income category structure
