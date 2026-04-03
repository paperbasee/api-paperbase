from django.db import migrations, models


def _sqlite_column_exists(schema_editor, table_name: str, column_name: str) -> bool:
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
    # PRAGMA table_info row shape: (cid, name, type, notnull, dflt_value, pk)
    return any(col[1] == column_name for col in columns)


def add_snapshot_columns_if_missing(apps, schema_editor):
    table = "orders_orderitem"
    statements = [
        (
            "product_name_snapshot",
            "ALTER TABLE orders_orderitem ADD COLUMN product_name_snapshot varchar(255) NULL",
        ),
        (
            "variant_snapshot",
            "ALTER TABLE orders_orderitem ADD COLUMN variant_snapshot varchar(255) NULL",
        ),
        (
            "unit_price_snapshot",
            "ALTER TABLE orders_orderitem ADD COLUMN unit_price_snapshot decimal NULL",
        ),
    ]
    with schema_editor.connection.cursor() as cursor:
        for column_name, sql in statements:
            if not _sqlite_column_exists(schema_editor, table, column_name):
                cursor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    add_snapshot_columns_if_missing,
                    migrations.RunPython.noop,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="orderitem",
                    name="product_name_snapshot",
                    field=models.CharField(blank=True, max_length=255, null=True),
                ),
                migrations.AddField(
                    model_name="orderitem",
                    name="variant_snapshot",
                    field=models.CharField(blank=True, max_length=255, null=True),
                ),
                migrations.AddField(
                    model_name="orderitem",
                    name="unit_price_snapshot",
                    field=models.DecimalField(decimal_places=2, max_digits=12, null=True),
                ),
            ],
        ),
    ]
