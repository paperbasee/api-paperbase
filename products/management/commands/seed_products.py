"""
Management command to clear all products and seed 100 gadgets + 100 accessories.
Usage: python manage.py seed_products
"""
import random
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils.text import slugify
from products.models import Product, NavbarCategory, Category


GADGET_PRODUCTS = [
    # Audio (sub: audio)
    ("Sony WH-1000XM5 Headphones", "Sony", "audio", 349.99, 399.99, "sale"),
    ("Apple AirPods Pro 2nd Gen", "Apple", "audio", 249.00, None, "new"),
    ("Bose QuietComfort 45", "Bose", "audio", 279.00, 329.00, "sale"),
    ("Samsung Galaxy Buds2 Pro", "Samsung", "audio", 189.99, 229.99, "sale"),
    ("Jabra Evolve2 85", "Jabra", "audio", 379.00, None, None),
    ("Sennheiser Momentum 4", "Sennheiser", "audio", 299.95, 349.95, "sale"),
    ("JBL Tune 770NC", "JBL", "audio", 99.99, 129.99, "sale"),
    ("Beats Studio Pro", "Beats", "audio", 349.95, None, "new"),
    ("Anker Soundcore Q45", "Anker", "audio", 59.99, 79.99, "hot"),
    ("Sony WF-1000XM5 Earbuds", "Sony", "audio", 279.99, None, "new"),
    ("Google Pixel Buds Pro", "Google", "audio", 199.00, None, None),
    ("Bose SoundLink Flex Speaker", "Bose", "audio", 149.00, None, None),
    ("JBL Charge 5 Speaker", "JBL", "audio", 179.95, 199.95, "sale"),
    ("Sony SRS-XB43 Speaker", "Sony", "audio", 149.99, 179.99, "sale"),
    ("Marshall Emberton II Speaker", "Marshall", "audio", 149.99, None, None),

    # Wearables (sub: wearables)
    ("Apple Watch Series 9 41mm", "Apple", "wearables", 399.00, None, "new"),
    ("Samsung Galaxy Watch 6 Classic", "Samsung", "wearables", 399.99, 429.99, "sale"),
    ("Garmin Fenix 7 Pro", "Garmin", "wearables", 699.99, None, None),
    ("Fitbit Sense 2", "Fitbit", "wearables", 199.95, 249.95, "sale"),
    ("Xiaomi Mi Band 8 Pro", "Xiaomi", "wearables", 49.99, 59.99, "hot"),
    ("Google Pixel Watch 2", "Google", "wearables", 349.99, None, "new"),
    ("Huawei Watch GT 4", "Huawei", "wearables", 249.99, 279.99, "sale"),
    ("Amazfit GTR 4", "Amazfit", "wearables", 149.99, 179.99, "sale"),
    ("Garmin Venu 3", "Garmin", "wearables", 449.99, None, None),
    ("Apple Watch Ultra 2", "Apple", "wearables", 799.00, None, "hot"),

    # Smart Home (sub: smart-home)
    ("Google Nest Hub 2nd Gen", "Google", "smart-home", 99.99, 129.99, "sale"),
    ("Amazon Echo Show 10", "Amazon", "smart-home", 249.99, 279.99, "sale"),
    ("Philips Hue Starter Kit", "Philips", "smart-home", 199.99, None, None),
    ("Ring Video Doorbell Pro 2", "Ring", "smart-home", 249.99, 299.99, "sale"),
    ("Nest Thermostat", "Google", "smart-home", 129.99, 149.99, "hot"),
    ("Amazon Echo Dot 5th Gen", "Amazon", "smart-home", 49.99, 59.99, "sale"),
    ("TP-Link Kasa Smart Plug", "TP-Link", "smart-home", 29.99, None, None),
    ("Arlo Pro 4 Security Camera", "Arlo", "smart-home", 199.99, 249.99, "sale"),
    ("Eufy RoboVac X8", "Eufy", "smart-home", 399.99, 499.99, "hot"),
    ("Govee LED Strip Lights 10m", "Govee", "smart-home", 39.99, 49.99, "sale"),

    # Gaming (sub: gaming)
    ("Sony PlayStation 5 Controller", "Sony", "gaming", 69.99, None, None),
    ("Xbox Elite Series 2 Controller", "Microsoft", "gaming", 179.99, None, None),
    ("SteelSeries Arctis Nova Pro", "SteelSeries", "gaming", 249.99, 299.99, "sale"),
    ("Razer BlackShark V2 Pro", "Razer", "gaming", 179.99, None, "new"),
    ("Logitech G Pro X Superlight 2", "Logitech", "gaming", 159.99, None, None),
    ("ASUS ROG Strix OLED Handheld", "ASUS", "gaming", 699.99, 749.99, "hot"),
    ("Corsair HS80 RGB Wireless", "Corsair", "gaming", 149.99, 179.99, "sale"),
    ("HyperX Cloud Alpha Wireless", "HyperX", "gaming", 199.99, 229.99, "sale"),
    ("Nintendo Switch OLED Controller", "Nintendo", "gaming", 49.99, None, None),
    ("Turtle Beach Stealth 700 Gen 2", "Turtle Beach", "gaming", 149.95, 179.95, "sale"),

    # Cameras (sub: cameras)
    ("GoPro HERO12 Black", "GoPro", "cameras", 399.99, 449.99, "sale"),
    ("Sony ZV-E10 Mirrorless", "Sony", "cameras", 698.00, 749.00, "sale"),
    ("DJI Osmo Pocket 3", "DJI", "cameras", 519.00, None, "new"),
    ("Insta360 X4", "Insta360", "cameras", 499.99, None, "new"),
    ("Canon EOS M50 Mark II", "Canon", "cameras", 649.00, 699.00, "sale"),
    ("Fujifilm Instax Mini 12", "Fujifilm", "cameras", 89.99, 99.99, "hot"),
    ("Nikon Z30 Vlogging Camera", "Nikon", "cameras", 756.95, None, None),
    ("Akaso Brave 7 LE", "Akaso", "cameras", 129.99, 159.99, "sale"),
    ("Sony RX100 VII Compact", "Sony", "cameras", 1199.99, 1299.99, "sale"),
    ("Ricoh Theta SC2 360 Camera", "Ricoh", "cameras", 249.99, None, None),

    # Drones (sub: drones)
    ("DJI Mini 4 Pro", "DJI", "drones", 759.00, None, "new"),
    ("DJI Air 3", "DJI", "drones", 1099.00, None, "hot"),
    ("Autel EVO Nano+", "Autel", "drones", 649.00, 699.00, "sale"),
    ("Holy Stone HS720E", "Holy Stone", "drones", 219.99, 259.99, "sale"),
    ("Ryze Tello Mini Drone", "Ryze", "drones", 99.00, None, None),
    ("DJI FPV Combo", "DJI", "drones", 999.00, 1099.00, "sale"),
    ("Parrot Anafi USA", "Parrot", "drones", 2499.00, None, None),
    ("Potensic ATOM SE", "Potensic", "drones", 179.99, 219.99, "sale"),
    ("Fimi X8 Mini V2", "Fimi", "drones", 349.99, 399.99, "hot"),
    ("iFlight Nazgul Evoque F5", "iFlight", "drones", 249.99, None, None),

    # New In Gadgets (sub: new)
    ("Samsung Galaxy S24 Ultra", "Samsung", "new", 1299.99, None, "new"),
    ("Apple iPhone 15 Pro Max", "Apple", "new", 1199.99, None, "new"),
    ("Google Pixel 8 Pro", "Google", "new", 999.99, None, "new"),
    ("Xiaomi 14 Pro", "Xiaomi", "new", 849.99, None, "new"),
    ("OnePlus 12 5G", "OnePlus", "new", 799.99, None, "new"),
    ("ASUS Zenfone 11 Ultra", "ASUS", "new", 899.99, None, "new"),
    ("Sony Xperia 1 VI", "Sony", "new", 1299.99, None, "new"),
    ("Motorola Edge 50 Pro", "Motorola", "new", 599.99, None, "new"),
    ("Nothing Phone 2a", "Nothing", "new", 349.99, None, "new"),
    ("Realme GT 6", "Realme", "new", 499.99, None, "new"),

    # Extra Audio
    ("Bose SoundSport Free Earbuds", "Bose", "audio", 149.00, 179.00, "sale"),
    ("Sony LinkBuds S", "Sony", "audio", 149.99, 199.99, "sale"),
    ("JBL Flip 6 Speaker", "JBL", "audio", 129.95, 149.95, "sale"),
    ("Anker Soundcore Liberty 4", "Anker", "audio", 99.99, 129.99, "sale"),
    ("Jabra Evolve2 65 Headset", "Jabra", "audio", 299.00, None, None),

    # Extra Wearables
    ("Garmin Instinct 2 Solar", "Garmin", "wearables", 349.99, 399.99, "sale"),
    ("Samsung Galaxy Ring", "Samsung", "wearables", 299.99, None, "new"),
    ("Oura Ring Gen 3", "Oura", "wearables", 349.00, None, None),
    ("Withings ScanWatch 2", "Withings", "wearables", 299.95, None, None),
    ("Polar Vantage V3", "Polar", "wearables", 599.99, None, None),

    # Extra Smart Home
    ("Philips Hue Play Light Bar 2-Pack", "Philips", "smart-home", 79.99, 99.99, "sale"),
    ("Amazon Smart Air Quality Monitor", "Amazon", "smart-home", 69.99, 79.99, "sale"),
    ("Arlo Essential Indoor Camera", "Arlo", "smart-home", 99.99, 129.99, "sale"),
    ("Wemo Smart Plug Mini", "Wemo", "smart-home", 24.99, None, None),
    ("Nanoleaf Shapes Hexagons Starter", "Nanoleaf", "smart-home", 89.99, 109.99, "sale"),

    # Extra Gaming
    ("Razer DeathAdder V3 Pro Mouse", "Razer", "gaming", 159.99, None, "new"),
    ("Logitech G915 TKL Keyboard", "Logitech", "gaming", 229.99, 249.99, "sale"),
    ("SteelSeries Apex Pro Mini", "SteelSeries", "gaming", 179.99, None, None),
    ("Corsair K100 RGB Keyboard", "Corsair", "gaming", 229.99, 249.99, "sale"),
    ("HyperX Pulsefire Haste 2 Mouse", "HyperX", "gaming", 59.99, 79.99, "hot"),

    # Extra Cameras
    ("DJI Pocket 2 Creator Combo", "DJI", "cameras", 349.00, 399.00, "sale"),
    ("Polaroid Now+ Gen 2 Camera", "Polaroid", "cameras", 149.99, None, None),
    ("Sony ZV-1F Vlog Camera", "Sony", "cameras", 449.99, 499.99, "sale"),
    ("Insta360 GO 3 Action Camera", "Insta360", "cameras", 379.99, None, "new"),
    ("Logitech Brio 4K Webcam", "Logitech", "cameras", 169.99, 199.99, "sale"),
]

ACCESSORY_PRODUCTS = [
    # Chargers (sub: chargers)
    ("Anker 735 GaNPrime 65W Charger", "Anker", "chargers", 35.99, 45.99, "sale"),
    ("Apple 35W Dual USB-C Adapter", "Apple", "chargers", 59.00, None, None),
    ("Samsung 45W Super Fast Charger", "Samsung", "chargers", 29.99, 39.99, "sale"),
    ("Belkin BoostCharge Pro 3-Port 67W", "Belkin", "chargers", 49.99, 59.99, "sale"),
    ("RAVPower 61W PD GaN Charger", "RAVPower", "chargers", 27.99, 39.99, "hot"),
    ("Ugreen 100W GaN Charger 4-Port", "Ugreen", "chargers", 45.99, 55.99, "sale"),
    ("Baseus 65W GaN Fast Charger", "Baseus", "chargers", 24.99, 34.99, "sale"),
    ("Spigen ArcStation Pro 45W", "Spigen", "chargers", 39.99, None, None),
    ("Mophie Speedport 120W", "Mophie", "chargers", 79.95, None, "new"),
    ("Aukey Omnia II Mix 65W", "Aukey", "chargers", 32.99, 42.99, "sale"),
    ("Google 30W USB-C Charger", "Google", "chargers", 24.99, None, None),
    ("Xiaomi 120W HyperCharge Adapter", "Xiaomi", "chargers", 19.99, 27.99, "hot"),
    ("Anker 543 USB-C to USB-C Charger", "Anker", "chargers", 14.99, None, None),
    ("Zendure SuperPort S4 100W", "Zendure", "chargers", 59.99, 69.99, "sale"),
    ("Nomad Base One Max MagSafe", "Nomad", "chargers", 149.95, None, "new"),

    # Cables (sub: cables)
    ("Anker 240W USB-C to USB-C Cable 1.8m", "Anker", "cables", 14.99, 19.99, "sale"),
    ("Apple USB-C to Lightning Cable 1m", "Apple", "cables", 19.00, None, None),
    ("Belkin USB-C to USB-C Braided 2m", "Belkin", "cables", 19.99, 24.99, "sale"),
    ("UGREEN USB-C to HDMI 4K Cable", "Ugreen", "cables", 12.99, 16.99, "sale"),
    ("Baseus 100W Fast Charge Cable", "Baseus", "cables", 9.99, 14.99, "hot"),
    ("Native Union Night Cable USB-C", "Native Union", "cables", 34.99, None, None),
    ("Moshi Integra USB-C Cable 1.5m", "Moshi", "cables", 29.95, None, None),
    ("Syncwire USB-C to USB-A 6ft", "Syncwire", "cables", 11.99, 15.99, "sale"),
    ("CableMatter DisplayPort 1.4 Cable", "CableMatter", "cables", 16.99, 21.99, "sale"),
    ("Thunderbolt 4 Cable 1m", "Apple", "cables", 49.00, None, None),
    ("Anker MagSafe Charging Cable 2m", "Anker", "cables", 19.99, None, None),
    ("Aukey USB-C Charging Cable 3-Pack", "Aukey", "cables", 13.99, 18.99, "hot"),
    ("Nomad Rugged USB-C to Lightning", "Nomad", "cables", 29.95, None, None),
    ("Capshi USB-C to 3.5mm Adapter", "Capshi", "cables", 8.99, 11.99, "sale"),
    ("Satechi USB-C to HDMI 8K Cable", "Satechi", "cables", 39.99, None, "new"),

    # Stands & Mounts (sub: stands)
    ("Twelve South HiRise 3 Laptop Stand", "Twelve South", "stands", 79.99, None, None),
    ("Lamicall Adjustable Phone Stand", "Lamicall", "stands", 19.99, 24.99, "sale"),
    ("Anker 551 USB-C Hub with Stand", "Anker", "stands", 89.99, 99.99, "sale"),
    ("Elago MagSafe Stand for iPhone", "Elago", "stands", 24.99, None, None),
    ("MagSafe Duo Charger", "Apple", "stands", 129.00, None, None),
    ("MOFT Snap Phone Stand", "MOFT", "stands", 29.95, None, "new"),
    ("Twelve South Compass Pro iPad Stand", "Twelve South", "stands", 59.99, None, None),
    ("Satechi Aluminum Desktop Stand", "Satechi", "stands", 49.99, 59.99, "sale"),
    ("Peak Design Mobile Tripod", "Peak Design", "stands", 79.95, None, None),
    ("Belkin MagSafe 3-in-1 Wireless", "Belkin", "stands", 149.99, 179.99, "sale"),
    ("iWALK LinkPod Portable Charger Stand", "iWALK", "stands", 29.99, 39.99, "hot"),
    ("Joby GorillaPod 3K Kit", "Joby", "stands", 59.95, None, None),
    ("Moment Phone Tripod Mount", "Moment", "stands", 29.99, None, None),
    ("ESR HaloLock MagSafe Stand", "ESR", "stands", 19.99, 27.99, "sale"),
    ("Benks Bold Kickstand Case 15 Pro", "Benks", "stands", 34.99, None, "new"),

    # Power Banks (sub: power-bank)
    ("Anker 737 Power Bank 24000mAh", "Anker", "power-bank", 99.99, 129.99, "sale"),
    ("Mophie Powerstation XXL 20000mAh", "Mophie", "power-bank", 69.95, 89.95, "sale"),
    ("Baseus 30000mAh 65W Power Bank", "Baseus", "power-bank", 59.99, 79.99, "hot"),
    ("Apple MagSafe Battery Pack", "Apple", "power-bank", 99.00, None, None),
    ("Zendure SuperTank 27000mAh", "Zendure", "power-bank", 99.99, 119.99, "sale"),
    ("RAVPower 20000mAh 60W PD", "RAVPower", "power-bank", 49.99, 64.99, "sale"),
    ("Ugreen 25000mAh 130W Power Bank", "Ugreen", "power-bank", 69.99, 89.99, "sale"),
    ("Belkin BPD002 10000mAh", "Belkin", "power-bank", 44.99, 54.99, "sale"),
    ("Xiaomi 33W Power Bank 20000mAh", "Xiaomi", "power-bank", 39.99, 49.99, "hot"),
    ("Nomad Power Pack 9600mAh", "Nomad", "power-bank", 79.95, None, None),

    # New In Accessories (sub: accessories-new)
    ("Apple MagSafe Wallet", "Apple", "accessories-new", 59.00, None, "new"),
    ("Samsung Smart Tag 2", "Samsung", "accessories-new", 29.99, None, "new"),
    ("Tile Mate Bluetooth Tracker", "Tile", "accessories-new", 24.99, 34.99, "hot"),
    ("Apple AirTag 4 Pack", "Apple", "accessories-new", 99.00, None, "new"),
    ("Anker Nano Power Bank 5000mAh", "Anker", "accessories-new", 25.99, None, "new"),
    ("Spigen Ultra Hybrid MagFit Case", "Spigen", "accessories-new", 14.99, 19.99, "hot"),
    ("Belkin Screen Protector iPhone 15", "Belkin", "accessories-new", 12.99, None, "new"),
    ("KeySmart Pro Compact Key Holder", "KeySmart", "accessories-new", 34.99, 44.99, "sale"),
    ("Moment iPhone 15 Pro Camera Case", "Moment", "accessories-new", 49.99, None, "new"),
    ("PopSockets MagSafe PopGrip", "PopSockets", "accessories-new", 19.99, None, "new"),
    ("Casetify Impact iPhone 15 Case", "Casetify", "accessories-new", 54.99, None, "new"),
    ("dbrand Grip Case iPhone 15 Pro", "dbrand", "accessories-new", 59.95, None, "new"),
    ("ESR Air Armor MagSafe Case", "ESR", "accessories-new", 19.99, 24.99, "sale"),
    ("Otterbox Defender Pro iPhone 15", "Otterbox", "accessories-new", 64.95, None, None),
    ("Pitaka MagEZ Case 4 iPhone 15", "Pitaka", "accessories-new", 79.99, None, "new"),
    ("Peak Design Everyday Case iPhone 15", "Peak Design", "accessories-new", 59.99, None, None),

    # Extra Chargers
    ("Anker 511 Charger Nano 3 30W", "Anker", "chargers", 15.99, None, "hot"),
    ("Belkin 25W USB-C PD Wall Charger", "Belkin", "chargers", 19.99, 24.99, "sale"),
    ("Ugreen 65W Nexode Mini Charger", "Ugreen", "chargers", 29.99, 39.99, "sale"),
    ("iWALK 33W PD Charger Plug", "iWALK", "chargers", 17.99, 22.99, "sale"),
    ("Baseus GaN3 Pro 65W Desktop Charger", "Baseus", "chargers", 34.99, 44.99, "sale"),

    # Extra Cables
    ("Anker 6ft Nylon USB-C to USB-C", "Anker", "cables", 12.99, 16.99, "sale"),
    ("Belkin 3.3ft Braided USB-C to C", "Belkin", "cables", 14.99, 18.99, "sale"),
    ("Ugreen USB-C to USB-A Cable 1m", "Ugreen", "cables", 8.99, 12.99, "hot"),
    ("Baseus Crystal Shine USB-C 1.2m", "Baseus", "cables", 7.99, 10.99, "sale"),
    ("Native Union Desk Cable USB-C 3m", "Native Union", "cables", 39.99, None, None),

    # Extra Stands
    ("Anker MagSafe 2-in-1 Wireless Stand", "Anker", "stands", 45.99, 55.99, "sale"),
    ("Lamicall Tablet Stand Adjustable", "Lamicall", "stands", 22.99, 29.99, "sale"),
    ("Twelve South Curve SE Laptop Stand", "Twelve South", "stands", 49.99, None, None),
    ("Moft Laptop Stand Foldable", "Moft", "stands", 39.99, 49.99, "sale"),
    ("Elago MagSafe Duo Stand", "Elago", "stands", 19.99, None, None),

    # Extra Power Banks
    ("Anker 325 Power Bank 20000mAh", "Anker", "power-bank", 35.99, 45.99, "sale"),
    ("Xiaomi Redmi 20000mAh Power Bank", "Xiaomi", "power-bank", 29.99, 39.99, "hot"),
    ("Belkin 10K Wireless Power Bank", "Belkin", "power-bank", 59.99, 74.99, "sale"),
    ("Ugreen 10000mAh 25W Power Bank", "Ugreen", "power-bank", 34.99, 44.99, "sale"),
    ("Baseus Adaman 65W 20000mAh", "Baseus", "power-bank", 55.99, 69.99, "sale"),

    # Extra New In Accessories
    ("Mophie Snap+ Juice Pack MagSafe", "Mophie", "accessories-new", 59.95, None, "new"),
    ("Nomad Base One MagSafe Charger", "Nomad", "accessories-new", 59.95, None, None),
    ("Twelve South AirFly Pro Wireless", "Twelve South", "accessories-new", 54.99, None, None),
    ("Belkin MagSafe Phone Mount Car", "Belkin", "accessories-new", 44.99, 54.99, "sale"),
    ("iOttie Easy One Touch 5 Car Mount", "iOttie", "accessories-new", 29.99, 39.99, "sale"),
    ("Spigen MagFit Qi2 Desktop Stand", "Spigen", "accessories-new", 27.99, None, "new"),
    ("ESR HaloLock 15W Wireless Charger", "ESR", "accessories-new", 25.99, 32.99, "sale"),
    ("Satechi USB-C Mobile Pro Hub iPad", "Satechi", "accessories-new", 79.99, 89.99, "sale"),
    ("Anker USB-C Hub 7-in-1", "Anker", "accessories-new", 39.99, 49.99, "hot"),
]

GADGET_DESCRIPTIONS = {
    "audio": "Premium audio device with immersive sound quality, advanced noise cancellation, and long battery life for all-day listening.",
    "wearables": "Smart wearable with health tracking, fitness monitoring, and seamless smartphone connectivity to keep you at your best.",
    "smart-home": "Intelligent smart home device that automates your living space, enhances comfort, and integrates with major smart home ecosystems.",
    "gaming": "High-performance gaming peripheral engineered for competitive play with ultra-low latency and precision controls.",
    "cameras": "Versatile camera capturing stunning photos and videos with advanced stabilization and intuitive controls.",
    "drones": "Feature-packed drone delivering breathtaking aerial footage with intelligent flight modes and obstacle avoidance.",
    "new": "Latest flagship device packed with cutting-edge technology, powerful performance, and a premium design.",
}

ACCESSORY_DESCRIPTIONS = {
    "chargers": "Fast and efficient charger with GaN technology for rapid, safe charging across all your devices.",
    "cables": "Durable, high-speed cable designed for reliable data transfer and fast charging with premium materials.",
    "stands": "Ergonomic stand and mount solution that keeps your devices perfectly positioned for productivity and comfort.",
    "power-bank": "Portable power bank with high capacity and fast charging to keep all your devices powered on the go.",
    "accessories-new": "Latest premium accessory designed to complement and protect your devices with style and functionality.",
}


class Command(BaseCommand):
    help = "Clear all products and seed 100 gadgets + 100 accessories"

    def handle(self, *args, **options):
        self.stdout.write("Deleting all existing products...")
        deleted_count, _ = Product.objects.all().delete()
        self.stdout.write(self.style.WARNING(f"  Deleted {deleted_count} products."))

        gadgets_nc = NavbarCategory.objects.get(slug="gadgets")
        accessories_nc = NavbarCategory.objects.get(slug="accessories")

        categories = {c.slug: c for c in Category.objects.all()}

        gadget_products_created = self._seed_products(
            GADGET_PRODUCTS, gadgets_nc, categories, GADGET_DESCRIPTIONS
        )
        self.stdout.write(self.style.SUCCESS(
            f"  Created {gadget_products_created} Gadget products."
        ))

        accessory_products_created = self._seed_products(
            ACCESSORY_PRODUCTS, accessories_nc, categories, ACCESSORY_DESCRIPTIONS
        )
        self.stdout.write(self.style.SUCCESS(
            f"  Created {accessory_products_created} Accessory products."
        ))

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Total products in DB: {Product.objects.count()}"
        ))

    def _seed_products(self, product_list, navbar_category, categories, descriptions):
        created = 0
        slug_counter = {}

        for name, brand, sub_slug, price, original_price, badge in product_list:
            base_slug = slugify(name)
            count = slug_counter.get(base_slug, 0) + 1
            slug_counter[base_slug] = count
            slug = base_slug if count == 1 else f"{base_slug}-{count}"

            sub_cat = categories.get(sub_slug)
            description = descriptions.get(sub_slug, "")

            Product.objects.create(
                name=name,
                brand=brand,
                slug=slug,
                price=Decimal(str(price)),
                original_price=Decimal(str(original_price)) if original_price else None,
                badge=badge or "",
                category=navbar_category,
                sub_category=sub_cat,
                description=description,
                stock=random.randint(5, 150),
                is_featured=random.random() < 0.15,
                is_active=True,
            )
            created += 1

        return created
