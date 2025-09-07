import mysql.connector

conn = mysql.connector.connect(
    host="sql12.freesqldatabase.com",
    user="sql12797518",
    password="29mmqTPEpk",
    database="sql12797518",
    port=3306
)

cur = conn.cursor()
# 1. Users table
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(150) UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

# 2. Bills table
cur.execute("""
CREATE TABLE IF NOT EXISTS bills (
    bill_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    account_number VARCHAR(20),
    customer_name VARCHAR(100),
    ca_number VARCHAR(20),
    meter_number VARCHAR(20),
    meter_serial_number VARCHAR(50),
    connection_type VARCHAR(50),
    connection_status VARCHAR(20),
    voltage VARCHAR(20),
    sanctioned_load VARCHAR(20),
    bill_number VARCHAR(50),
    bill_cycle VARCHAR(20),
    bill_date DATE,
    due_date DATE,
    bill_amount DECIMAL(12,2),
    late_payment_amount DECIMAL(12,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
)
""")

# 3. Contract Demands
cur.execute("""
CREATE TABLE IF NOT EXISTS contract_demands (
    demand_id INT AUTO_INCREMENT PRIMARY KEY,
    bill_id INT,
    demand_value VARCHAR(50),
    FOREIGN KEY (bill_id) REFERENCES bills(bill_id) ON DELETE CASCADE
)
""")

# 4. Consumption Details
cur.execute("""
CREATE TABLE IF NOT EXISTS consumption_details (
    consumption_id INT AUTO_INCREMENT PRIMARY KEY,
    bill_id INT,
    current_reading DECIMAL(12,2),
    previous_reading DECIMAL(12,2),
    units_billed DECIMAL(12,2),
    FOREIGN KEY (bill_id) REFERENCES bills(bill_id) ON DELETE CASCADE
)
""")

# 5. Slab Details
cur.execute("""
CREATE TABLE IF NOT EXISTS slab_details (
    slab_id INT AUTO_INCREMENT PRIMARY KEY,
    bill_id INT,
    units DECIMAL(12,2),
    rate DECIMAL(12,2),
    amount DECIMAL(12,2),
    FOREIGN KEY (bill_id) REFERENCES bills(bill_id) ON DELETE CASCADE
)
""")

# 6. Charges Breakdown
cur.execute("""
CREATE TABLE IF NOT EXISTS charges_breakdown (
    charge_id INT AUTO_INCREMENT PRIMARY KEY,
    bill_id INT,
    charge_name VARCHAR(100),
    charge_value DECIMAL(12,2),
    FOREIGN KEY (bill_id) REFERENCES bills(bill_id) ON DELETE CASCADE
)
""")
conn.commit()
cur.close()
conn.close()
print("All tables created successfully!")
