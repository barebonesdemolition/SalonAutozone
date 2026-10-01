-- 1. Businesses Table
CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL UNIQUE,
    business_type TEXT NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    country TEXT DEFAULT 'Sierra Leone',
    city TEXT NOT NULL,
    whatsapp TEXT NOT NULL,
    email TEXT,
    description TEXT,
    logo_url TEXT,
    status TEXT DEFAULT 'pending', -- 'pending', 'verified', 'rejected', 'suspended'
    subscription_tier TEXT DEFAULT 'free', -- 'free', 'starter', 'pro'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Staff Roles
CREATE TABLE IF NOT EXISTS business_staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT DEFAULT 'sales', -- 'owner', 'manager', 'sales'
    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
);

-- 3. Vehicle Listings (Includes Import/Dealership fields)
CREATE TABLE IF NOT EXISTS vehicle_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER, -- NULL for private individual sellers
    title TEXT NOT NULL,
    condition TEXT DEFAULT 'used', -- 'new', 'used'
    availability TEXT DEFAULT 'in_stock', -- 'in_stock', 'in_transit', 'on_order'
    country_of_origin TEXT,
    vin TEXT,
    duty_paid INTEGER DEFAULT 0, -- 0 = No, 1 = Yes
    arrival_date TEXT,
    supplier_url TEXT, -- Staff-only supplier link
    price_sll REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
);

-- 4. Orange Money Payments Queue
CREATE TABLE IF NOT EXISTS payment_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL,
    provider TEXT DEFAULT 'Orange Money',
    transaction_reference TEXT NOT NULL UNIQUE,
    amount_sll REAL NOT NULL,
    tier TEXT NOT NULL,
    status TEXT DEFAULT 'pending_verification', -- 'pending_verification', 'approved', 'rejected'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
);

-- 5. Buyer Enquiries / Inbox
CREATE TABLE IF NOT EXISTS vehicle_enquiries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    business_id INTEGER NOT NULL,
    sender_name TEXT NOT NULL,
    sender_phone_wa TEXT NOT NULL,
    message TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(listing_id) REFERENCES vehicle_listings(id) ON DELETE CASCADE,
    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
);
