"""
Amazon Orders CSV importer for Retail.OrderHistory files.

This module handles importing Amazon purchase data from the "Your Orders" data dump.
Supports Retail.OrderHistory.*.csv files with columns from Amazon's data export format.
"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import polars as pl

from moneyflow.backends.amazon import AmazonBackend


def import_amazon_orders(
    directory_path: str,
    backend: Optional[AmazonBackend] = None,
    force: bool = False,
) -> Dict[str, int]:
    """
    Import Amazon orders from Retail.OrderHistory CSV files.

    Scans the specified directory for all Retail.OrderHistory.*.csv files,
    combines them, and imports into the Amazon backend database.

    Expected CSV columns:
    - ASIN: Amazon Standard Identification Number
    - Order ID: Order identifier
    - Order Date: ISO timestamp (e.g., "2025-10-13T22:08:07Z")
    - Product Name: Item description
    - Quantity: Number of items
    - Total Owed: Final amount paid
    - Unit Price: Item price before tax
    - Order Status: "Closed", "New", "Cancelled", etc.
    - Shipment Status: "Shipped", "Delivered", etc.

    Args:
        directory_path: Path to "Your Orders" directory containing CSV files
        backend: AmazonBackend instance (creates default if None)
        force: If True, re-import existing transactions (updates them)

    Returns:
        Dictionary with import statistics:
        - imported: Number of new transactions imported
        - duplicates: Number of existing transactions skipped
        - skipped: Number of cancelled/invalid rows skipped

    Raises:
        FileNotFoundError: If directory doesn't exist
        ValueError: If no CSV files found

    Example:
        >>> from moneyflow.backends.amazon import AmazonBackend
        >>> from moneyflow.importers.amazon_orders_csv import import_amazon_orders
        >>> backend = AmazonBackend()
        >>> stats = import_amazon_orders("~/Downloads/Your Orders", backend)
        >>> print(f"Imported {stats['imported']} transactions")
    """
    if backend is None:
        backend = AmazonBackend()

    directory = Path(directory_path).expanduser()
    if not directory.exists():
        raise FileNotFoundError(f"Directory not found: {directory}")

    # Find all Retail.OrderHistory CSV files
    csv_files = list(directory.glob("**/Retail.OrderHistory.*.csv"))

    if not csv_files:
        raise ValueError(f"No Retail.OrderHistory CSV files found in {directory}")

    # Read and combine all CSV files
    # Use infer_schema_length=0 to read all columns as strings first,
    # then we'll parse the specific fields we need
    all_dataframes = []
    for csv_file in csv_files:
        df = pl.read_csv(csv_file, infer_schema_length=0)
        all_dataframes.append(df)

    combined_df = pl.concat(all_dataframes)

    # Get existing transaction IDs from database
    existing_ids = set()
    if not force:
        conn = backend._get_connection()
        cursor = conn.execute("SELECT id FROM transactions")
        existing_ids = {row[0] for row in cursor.fetchall()}
        conn.close()

    # Process rows and import
    imported_count = 0
    duplicate_count = 0
    skipped_count = 0

    conn = backend._get_connection()

    # Note: Categories come from categories.py, not database
    # No need to create/insert categories

    # Track seen ASIN+OrderID combinations to handle CSV duplicates
    seen_combinations = {}

    for row in combined_df.iter_rows(named=True):
        # Skip rows missing critical fields
        asin = row.get("ASIN", "")
        order_id = row.get("Order ID", "")
        product_name = row.get("Product Name", "")

        if not order_id:
            skipped_count += 1
            continue

        # Handle missing or placeholder ASINs
        if not asin or asin == "_ASINLESS_":
            # Generate pseudo-ASIN from product name hash
            name_hash = hashlib.md5(product_name.encode()).hexdigest()[:10]
            asin = f"HASH_{name_hash}"

        # Skip cancelled orders
        order_status = row.get("Order Status", "")
        if order_status == "Cancelled":
            skipped_count += 1
            continue

        # Skip cancelled line items (Quantity=0 indicates item was cancelled from order)
        quantity_str = row.get("Quantity", "1").replace(",", "")
        try:
            quantity = int(quantity_str)
        except ValueError:
            quantity = 1
        if quantity == 0:
            skipped_count += 1
            continue

        # Generate transaction ID with sequence number for duplicates
        # (Amazon CSV sometimes has duplicate rows for same ASIN+Order)
        base_key = (asin, order_id)
        if base_key in seen_combinations:
            seen_combinations[base_key] += 1
            seq = seen_combinations[base_key]
            txn_id = f"{backend.generate_transaction_id(asin, order_id)}_{seq}"
        else:
            seen_combinations[base_key] = 0
            txn_id = backend.generate_transaction_id(asin, order_id)

        # Skip duplicates (unless force=True)
        if not force and txn_id in existing_ids:
            duplicate_count += 1
            continue

        # Parse date from ISO timestamp
        order_date_str = row.get("Order Date", "")
        try:
            order_date = datetime.fromisoformat(order_date_str.replace("Z", "+00:00"))
            date_str = order_date.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            # Skip rows with invalid dates
            skipped_count += 1
            continue

        # Extract fields (product_name already extracted above for ASIN hashing)
        if not product_name:
            product_name = "Unknown Item"

        try:
            total_owed_str = row.get("Total Owed", "0.0").replace(",", "")
            total_owed = float(total_owed_str)
            amount = -total_owed  # Negative for expenses
        except (ValueError, AttributeError):
            # Skip rows with invalid amounts
            skipped_count += 1
            continue

        # Skip "Try Before You Buy" returns (no charge applied but had a unit price)
        # These have Total Owed=0, Shipment Item Subtotal=0, but Unit Price > 0
        if total_owed == 0:
            try:
                subtotal_str = row.get("Shipment Item Subtotal", "0").replace(",", "")
                unit_price_str = row.get("Unit Price", "0").replace(",", "")
                subtotal = float(subtotal_str) if subtotal_str else 0
                unit_price = float(unit_price_str) if unit_price_str else 0
                if subtotal == 0 and unit_price > 0:
                    skipped_count += 1
                    continue
            except (ValueError, AttributeError):
                pass  # If parsing fails, don't skip

        shipment_status = row.get("Shipment Status", "")

        # Insert transaction (use REPLACE only if force=True)
        # Note: group is NOT stored - it's derived from category by data_manager
        insert_mode = "INSERT OR REPLACE" if force else "INSERT"
        conn.execute(
            f"""
            {insert_mode} INTO transactions
            (id, date, merchant, category, category_id, amount, quantity,
             asin, order_id, account, order_status, shipment_status, hideFromReports)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                txn_id,
                date_str,
                product_name,
                "Uncategorized",
                "cat_uncategorized",
                amount,
                quantity,
                asin,
                order_id,
                order_id,  # account = order_id for compatibility
                order_status,
                shipment_status,
                0,  # Not hidden by default
            ),
        )
        imported_count += 1

    # Record import history
    filenames = ", ".join([f.name for f in csv_files])
    conn.execute(
        """
        INSERT INTO import_history (filename, record_count, duplicate_count, skipped_count)
        VALUES (?, ?, ?, ?)
        """,
        (filenames, imported_count, duplicate_count, skipped_count),
    )

    conn.commit()
    conn.close()

    return {
        "imported": imported_count,
        "duplicates": duplicate_count,
        "skipped": skipped_count,
    }
