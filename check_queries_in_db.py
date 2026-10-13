import pandas as pd
import sqlite3

# Connect and read with Pandas
conn = sqlite3.connect("data/database.db")
df = pd.read_sql_query("SELECT * FROM products", conn)
print(df)
conn.close()
