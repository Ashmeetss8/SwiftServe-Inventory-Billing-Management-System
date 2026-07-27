from flask import Flask, request, redirect, url_for, render_template, session, flash, send_from_directory, jsonify
import sqlite3, os, datetime
from werkzeug.serving import WSGIRequestHandler

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = "your_secret_key_here"  # change this in production

DATABASE = "inventory.db"
MINIMUM_STOCK = 5  # Minimum stock threshold for alert notifications

# ----------------------------
# Database helper functions
# ----------------------------
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    if not os.path.exists(DATABASE):
        conn = get_db_connection()
        cur = conn.cursor()
        # Create inventory table
        cur.execute('''CREATE TABLE inventory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        barcode TEXT UNIQUE,
                        name TEXT,
                        category TEXT,
                        discount REAL,
                        selling_price REAL,
                        quantity INTEGER,
                        tax REAL
                      )''')
        # Create salespersons table
        cur.execute('''CREATE TABLE salespersons (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE,
                        password TEXT
                      )''')
        # Create admins table
        cur.execute('''CREATE TABLE admins (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE,
                        password TEXT
                      )''')
        # Create sales table (added customer details)
        cur.execute('''CREATE TABLE sales (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        salesperson_id INTEGER,
                        customer_name TEXT,
                        customer_phone TEXT,
                        total REAL,
                        sale_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                      )''')
        # Create sale_items table (item_id stores barcode)
        cur.execute('''CREATE TABLE sale_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sale_id INTEGER,
                        item_id TEXT,
                        quantity INTEGER,
                        price REAL,
                        discount REAL,
                        tax REAL
                      )''')
        # Create notifications table for alerts and messages with additional columns
        cur.execute('''CREATE TABLE notifications (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message TEXT,
                        type TEXT,
                        employee_id INTEGER,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                      )''')
        # Insert a default admin and salesperson
        cur.execute("INSERT INTO admins (username, password) VALUES (?,?)", ("admin", "admin"))
        cur.execute("INSERT INTO salespersons (username, password) VALUES (?,?)", ("employee", "employee"))
        conn.commit()
        conn.close()

init_db()

# ----------------------------
# Notification helper
# ----------------------------
def add_notification(message, notif_type="general", employee_id=None):
    conn = get_db_connection()
    conn.execute("INSERT INTO notifications (message, type, employee_id) VALUES (?,?,?)", (message, notif_type, employee_id))
    conn.commit()
    conn.close()

# ----------------------------
# Helper: Check login status
# ----------------------------
def is_employee_logged_in():
    return session.get("user_type") == "employee"

def is_admin_logged_in():
    return session.get("user_type") == "admin"

# ----------------------------
# Home route
# ----------------------------
@app.route('/')
def home():
    return render_template('home.html')

# ----------------------------
# Employee Login
# ----------------------------
@app.route('/employee_login', methods=['GET', 'POST'])
def employee_login():
    if request.method == 'POST':
        username = request.form["username"]
        password = request.form["password"]
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM salespersons WHERE username=? AND password=?", (username, password)).fetchone()
        conn.close()
        if user:
            session["user_type"] = "employee"
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["cart"] = []
            return redirect(url_for("employee_dashboard"))
        else:
            flash("Invalid credentials. Please try again.", "danger")
    return render_template('employee_login.html')

# ----------------------------
# Employee Dashboard and Cart Routes
# ----------------------------
@app.route('/update_cart', methods=['POST'])
def update_cart():
    if not is_employee_logged_in():
        return redirect(url_for('employee_login'))
    barcodes = request.form.getlist("barcode[]")
    quantities = request.form.getlist("quantity[]")
    updated_cart = []
    for barcode, qty in zip(barcodes, quantities):
        qty = int(qty)
        # Find the matching item in the session cart and recalc totals using the stored tax_rate
        for item in session.get("cart", []):
            if item["barcode"] == barcode:
                selling_price = float(item["selling_price"])
                discount = float(item["discount"])
                tax_rate = float(item.get("tax_rate", 0))
                price_after_discount = selling_price - discount
                tax_amount = price_after_discount * tax_rate / 100
                total = (price_after_discount + tax_amount) * qty
                item["quantity"] = qty
                item["total"] = total
                updated_cart.append(item)
                break
    session["cart"] = updated_cart
    flash("Cart updated.")
    return redirect(url_for("employee_dashboard"))

# New route: Update a single item in the session cart via AJAX
@app.route('/update_item', methods=['POST'])
def update_item():
    if not is_employee_logged_in():
        return redirect(url_for('employee_login'))
    barcode = request.form.get("barcode")
    try:
        quantity = int(request.form.get("quantity"))
    except (ValueError, TypeError):
        return "failed"
    updated = False
    cart = session.get("cart", [])
    for item in cart:
        if item["barcode"] == barcode:
            item["quantity"] = quantity
            selling_price = float(item["selling_price"])
            discount = float(item["discount"])
            tax_rate = float(item.get("tax_rate", 0))
            price_after_discount = selling_price - discount
            tax_amount = price_after_discount * tax_rate / 100
            item["total"] = (price_after_discount + tax_amount) * quantity
            updated = True
            break
    session["cart"] = cart
    return "success" if updated else "failed"

@app.route('/remove_item', methods=['POST'])
def remove_item():
    if not is_employee_logged_in():
        return redirect(url_for('employee_login'))
    
    barcode = request.form.get("barcode")
    if not barcode:
        flash("No item specified for removal")
        return redirect(url_for('employee_dashboard'))
    
    cart = session.get("cart", [])
    # Find and remove the specific item with the matching barcode
    cart = [item for item in cart if item["barcode"] != barcode]
    session["cart"] = cart
    
    flash("Item removed from cart")
    return redirect(url_for('employee_dashboard'))

@app.route('/employee_dashboard', methods=['GET'])
def employee_dashboard():
    if not is_employee_logged_in():
        return redirect(url_for('employee_login'))
    cart = session.get("cart", [])
    total_amount = sum(float(item["total"]) for item in cart)
    return render_template('employee_dashboard.html', cart=cart, total_amount=total_amount)

# ----------------------------
# Add scanned item to cart
# ----------------------------
@app.route('/add_item', methods=['POST'])
def add_item():
    if not is_employee_logged_in():
        return redirect(url_for('employee_login'))
    
    barcode = request.form.get("barcode")
    try:
        # Try to convert barcode to integer for ID lookup
        product_id = int(barcode)
        conn = get_db_connection()
        # First try to find item by ID
        item = conn.execute("SELECT * FROM inventory WHERE id=?", (product_id,)).fetchone()
        if not item:
            # If not found by ID, try barcode
            item = conn.execute("SELECT * FROM inventory WHERE barcode=?", (barcode,)).fetchone()
        
        if not item:
            conn.close()
            flash("Item not found in inventory!")
            return redirect(url_for('employee_dashboard'))

        try:
            quantity = int(request.form.get("quantity", 1))
        except (ValueError, TypeError):
            quantity = 1

        selling_price = float(item["selling_price"])
        discount = float(item["discount"])
        # Store the tax as a rate (percentage)
        tax_rate = float(item["tax"])
        price_after_discount = selling_price - discount
        tax_amount = price_after_discount * tax_rate / 100
        total = (price_after_discount + tax_amount) * quantity

        cart_item = {
            "barcode": item["barcode"],
            "name": item["name"],
            "selling_price": selling_price,
            "discount": discount,
            "tax_rate": tax_rate,
            "quantity": quantity,
            "total": total
        }

        cart = session.get("cart", [])
        # Check if item already exists in cart
        found = False
        for existing in cart:
            if existing["barcode"] == cart_item["barcode"]:
                existing["quantity"] += quantity
                # Recalculate total for the updated quantity
                price_after_discount = selling_price - discount
                tax_amount = price_after_discount * tax_rate / 100
                existing["total"] = (price_after_discount + tax_amount) * existing["quantity"]
                found = True
                break

        if not found:
            cart.append(cart_item)

        session["cart"] = cart
        conn.close()
        flash(f"Added {quantity} x {item['name']} to cart")
    except ValueError as e:
        flash("Invalid barcode format!")
    except Exception as e:
        flash(f"Error adding item: {str(e)}")
    
    return redirect(url_for('employee_dashboard'))

# ----------------------------
# Customer Details & Checkout
# ----------------------------
@app.route('/customer_details', methods=['GET', 'POST'])
def customer_details():
    if not is_employee_logged_in():
        return redirect(url_for('employee_login'))
    cart = session.get("cart", [])
    if not cart:
        flash("No items in cart!")
        return redirect(url_for('employee_dashboard'))
    if request.method == 'POST':
        customer_name = request.form.get("customer_name")
        customer_phone = request.form.get("customer_phone")
        total_sale = sum(float(item["total"]) for item in cart)
        salesperson_id = session.get("user_id")
        low_stock_notifications = []  # Collect low stock messages here
        conn = get_db_connection()
        cur = conn.cursor()
        # Insert the sale
        cur.execute("INSERT INTO sales (salesperson_id, customer_name, customer_phone, total) VALUES (?,?,?,?)", 
                    (salesperson_id, customer_name, customer_phone, total_sale))
        sale_id = cur.lastrowid
        
        # Process each item and check stock levels
        for item in cart:
            # Check if the quantity in inventory is enough based on the (possibly updated) cart value.
            inv_item = cur.execute("SELECT quantity, name FROM inventory WHERE barcode=?", (item["barcode"],)).fetchone()
            if inv_item and inv_item["quantity"] >= item["quantity"]:
                new_qty = inv_item["quantity"] - item["quantity"]
                cur.execute("UPDATE inventory SET quantity=? WHERE barcode=?", (new_qty, item["barcode"]))
                if new_qty < MINIMUM_STOCK:
                    low_stock_msg = f"Low Stock Alert: {item['name']} has only {new_qty} units left. Please restock."
                    low_stock_notifications.append(low_stock_msg)
                    flash(low_stock_msg, "warning")  # Flash with warning category
            else:
                flash(f"Insufficient stock for item {item['name']}", "danger")
                conn.rollback()
                conn.close()
                return redirect(url_for('employee_dashboard'))
            
            # Calculate tax amount for the item
            price_after_discount = item["selling_price"] - item["discount"]
            tax_amount = price_after_discount * item["tax_rate"] / 100
            
            # Insert sale item details with tax
            cur.execute("INSERT INTO sale_items (sale_id, item_id, quantity, price, discount, tax) VALUES (?,?,?,?,?,?)", 
                        (sale_id, item["barcode"], item["quantity"], item["selling_price"], item["discount"], tax_amount))
        
        conn.commit()

        # Get salesperson name
        salesperson = cur.execute("SELECT username FROM salespersons WHERE id=?", (salesperson_id,)).fetchone()
        conn.close()

        # After commit, add low stock notifications to the database
        for msg in low_stock_notifications:
            add_notification(msg, "inventory")

        # Prepare cart items with tax for the bill
        bill_items = []
        for item in cart:
            price_after_discount = item["selling_price"] - item["discount"]
            tax_amount = price_after_discount * item["tax_rate"] / 100
            bill_item = item.copy()  # Create a copy of the item
            bill_item["tax"] = tax_amount  # Add tax amount
            bill_items.append(bill_item)

        # Create bill object
        bill = {
            "sale_id": sale_id,
            "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "salesperson": salesperson["username"] if salesperson else "Unknown",
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            "cart_items": bill_items,
            "total": total_sale
        }

        # Record the sale notification
        sale_notification_msg = f"Sale Made: Sale ID {sale_id} by {session.get('username')} for total ${total_sale:.2f}."
        add_notification(sale_notification_msg, "sale", salesperson_id)
        
        # Clear the cart
        session["cart"] = []
        
        # If there were any low stock notifications, make sure they're shown
        if low_stock_notifications:
            for msg in low_stock_notifications:
                flash(msg, "warning")
            
        return render_template('bill.html', bill=bill)
    
    return render_template('customer_details.html')

# ----------------------------
# Search Bill
# ----------------------------
@app.route('/search_bill', methods=['GET', 'POST'])
def search_bill():
    if not is_employee_logged_in() and not is_admin_logged_in():
        return redirect(url_for('home'))
    if request.method == 'POST':
        sale_id = request.form.get("sale_id")
        return redirect(url_for('view_bill', sale_id=sale_id))
    return render_template('search_bill.html')

# ----------------------------
# View Bill
# ----------------------------
@app.route('/view_bill/<int:sale_id>')
def view_bill(sale_id):
    if not is_employee_logged_in() and not is_admin_logged_in():
        return redirect(url_for('home'))
    conn = get_db_connection()
    sale = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    if not sale:
        conn.close()
        flash("Sale not found!")
        return redirect(url_for('search_bill'))

    # Get salesperson name
    salesperson = None
    if sale["salesperson_id"]:
        salesperson = conn.execute("SELECT username FROM salespersons WHERE id=?", 
                                 (sale["salesperson_id"],)).fetchone()

    sale_items = conn.execute("""
        SELECT si.*, i.name, i.barcode 
        FROM sale_items si 
        LEFT JOIN inventory i ON si.item_id = i.barcode 
        WHERE si.sale_id=?
    """, (sale_id,)).fetchall()
    
    cart_items = []
    for item in sale_items:
        item_dict = {
            "barcode": item["barcode"],
            "name": item["name"] if item["name"] else "N/A",
            "quantity": item["quantity"],
            "selling_price": item["price"],
            "discount": item["discount"],
            "tax": item["tax"],
            "total": (item["price"] - item["discount"] + item["tax"]) * item["quantity"]
        }
        cart_items.append(item_dict)
    
    conn.close()
    
    bill = {
        "sale_id": sale["id"],
        "date": sale["sale_time"],
        "salesperson": salesperson["username"] if salesperson else "Unknown",
        "customer_name": sale["customer_name"],
        "customer_phone": sale["customer_phone"],
        "cart_items": cart_items,
        "total": sale["total"]
    }
    
    # Pass user type to template
    user_type = session.get('user_type', None)
    return render_template('view_bill.html', bill=bill, user_type=user_type)

# ----------------------------
# Admin Login
# ----------------------------
@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form["username"]
        password = request.form["password"]
        conn = get_db_connection()
        admin = conn.execute("SELECT * FROM admins WHERE username=? AND password=?", (username, password)).fetchone()
        conn.close()
        if admin:
            session["user_type"] = "admin"
            session["admin_id"] = admin["id"]
            session["username"] = admin["username"]
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Invalid admin credentials!")
    return render_template('admin_login.html')

# ----------------------------
# Admin Dashboard
# ----------------------------
@app.route('/admin_dashboard', methods=['GET'])
def admin_dashboard():
    if not is_admin_logged_in():
        return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    # Get all inventory items
    inventory = conn.execute("SELECT * FROM inventory").fetchall()
    
    # Get all salespersons
    salespersons = conn.execute("SELECT * FROM salespersons").fetchall()
    
    # Check for low stock items
    low_stock_items = conn.execute("""
        SELECT id, name, quantity 
        FROM inventory 
        WHERE quantity <= ?
    """, (MINIMUM_STOCK,)).fetchall()
    
    # Add notifications for low stock items
    for item in low_stock_items:
        notification_msg = f"Low Stock Alert: {item['name']} has only {item['quantity']} units left."
        add_notification(notification_msg, "inventory")
        flash(notification_msg, "warning")
    
    conn.close()
    return render_template('admin_dashboard.html', inventory=inventory, salespersons=salespersons)

# ----------------------------
# Admin: Add/Update Inventory Item
# ----------------------------
@app.route('/add_inventory', methods=['POST'])
def add_inventory():
    if not is_admin_logged_in():
        return redirect(url_for('admin_login'))
    barcode = request.form.get("barcode")
    name = request.form.get("name")
    category = request.form.get("category")
    discount = float(request.form.get("discount"))
    selling_price = float(request.form.get("selling_price"))
    quantity = int(request.form.get("quantity"))
    tax = float(request.form.get("tax"))
    conn = get_db_connection()
    cur = conn.cursor()
    item = cur.execute("SELECT * FROM inventory WHERE barcode=?", (barcode,)).fetchone()
    if item:
        new_qty = item["quantity"] + quantity
        cur.execute("""UPDATE inventory SET name=?, category=?, discount=?, selling_price=?, quantity=?, tax=? 
                       WHERE barcode=?""", (name, category, discount, selling_price, new_qty, tax, barcode))
        notification_msg = f"Inventory Updated: {quantity} units added to {name}. New stock: {new_qty}."
    else:
        cur.execute("""INSERT INTO inventory (barcode, name, category, discount, selling_price, quantity, tax) 
                       VALUES (?,?,?,?,?,?,?)""", (barcode, name, category, discount, selling_price, quantity, tax))
        notification_msg = f"New Item Added: {name} with stock {quantity}."
    conn.commit()
    conn.close()
    add_notification(notification_msg, "inventory")
    flash("Inventory updated successfully!")
    return redirect(url_for('admin_dashboard'))

# ----------------------------
# Admin: Create Salesperson
# ----------------------------
@app.route('/create_salesperson', methods=['POST'])
def create_salesperson():
    if not is_admin_logged_in():
        return redirect(url_for('admin_login'))
    username = request.form.get("username")
    password = request.form.get("password")
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO salespersons (username, password) VALUES (?,?)", (username, password))
        conn.commit()
        flash("Salesperson created successfully!")
    except Exception as e:
        flash("Error creating salesperson. Username might already exist.")
    finally:
        conn.close()
    return redirect(url_for('admin_dashboard'))

# ----------------------------
# Admin: Edit Salesperson
# ----------------------------
@app.route('/edit_salesperson/<int:id>', methods=['POST'])
def edit_salesperson(id):
    if not is_admin_logged_in():
        return redirect(url_for('admin_login'))
    
    username = request.form.get('username')
    new_password = request.form.get('new_password')
    
    conn = get_db_connection()
    try:
        if new_password:
            conn.execute("UPDATE salespersons SET username = ?, password = ? WHERE id = ?", 
                        (username, new_password, id))
        else:
            conn.execute("UPDATE salespersons SET username = ? WHERE id = ?", 
                        (username, id))
        conn.commit()
        flash("Salesperson updated successfully!")
    except Exception as e:
        flash("Error updating salesperson. Username might already exist.")
    finally:
        conn.close()
    return redirect(url_for('admin_dashboard'))

# ----------------------------
# Admin: Delete Salesperson
# ----------------------------
@app.route('/delete_salesperson/<int:id>', methods=['POST'])
def delete_salesperson(id):
    if not is_admin_logged_in():
        return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    try:
        # Check if there are any sales associated with this salesperson
        sales = conn.execute("SELECT COUNT(*) as count FROM sales WHERE salesperson_id = ?", (id,)).fetchone()
        if sales and sales['count'] > 0:
            flash("Cannot delete salesperson: They have associated sales records.")
        else:
            conn.execute("DELETE FROM salespersons WHERE id = ?", (id,))
            conn.commit()
            flash("Salesperson deleted successfully!")
    except Exception as e:
        flash("Error deleting salesperson.")
    finally:
        conn.close()
    return redirect(url_for('admin_dashboard'))

# ----------------------------
# Admin: Get Salesperson Details
# ----------------------------
@app.route('/get_salesperson/<int:id>')
def get_salesperson(id):
    if not is_admin_logged_in():
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    try:
        salesperson = conn.execute("SELECT id, username FROM salespersons WHERE id = ?", (id,)).fetchone()
        if salesperson:
            return jsonify({
                'id': salesperson['id'],
                'username': salesperson['username']
            })
        return jsonify({'error': 'Salesperson not found'}), 404
    finally:
        conn.close()

# ----------------------------
# Notifications for Admin
# ----------------------------
@app.route('/admin_notifications')
def admin_notifications():
    if not is_admin_logged_in():
        return redirect(url_for('admin_login'))
    conn = get_db_connection()
    notifications = conn.execute("SELECT * FROM notifications ORDER BY timestamp DESC").fetchall()
    conn.close()
    return render_template('admin_notifications.html', notifications=notifications)

# ----------------------------
# Notifications for Salesperson
# ----------------------------
@app.route('/employee_notifications')
def employee_notifications():
    if not is_employee_logged_in():
        return redirect(url_for('employee_login'))
    employee_id = session.get("user_id")
    conn = get_db_connection()
    notifications = conn.execute("SELECT * FROM notifications WHERE type = 'sale' AND employee_id = ? ORDER BY timestamp DESC", (employee_id,)).fetchall()
    conn.close()
    return render_template('employee_notifications.html', notifications=notifications)

# ----------------------------
# Logout
# ----------------------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.static_folder, 'images'), 'Logo.png', mimetype='image/png')

if __name__ == '__main__':
    # Enable support for low-level WSGI features
    WSGIRequestHandler.protocol_version = "HTTP/1.1"
    
    # Run with HTTPS
    app.run(
        debug=True,
        host='0.0.0.0',
        port=5000,
        ssl_context='adhoc'  # This will generate a self-signed certificate
    )
